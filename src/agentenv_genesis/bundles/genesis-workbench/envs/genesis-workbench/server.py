"""The Genesis workbench env: an agent fixes a sim-ready object's physics through MCP tools, and Genesis measures it.

urn:genesis:load-object/v1 loads one object as it was delivered, bug included, with the contributor's complaint: one
of the primitive objects in workbench.py, a scanned or VLM-to-CAD mesh object, or an articulated URDF from assets/.
data/get runs the hidden acceptance checks and returns the episode: every call, the frames, the object before and after,
and the probe curves."""

from __future__ import annotations

import asyncio
import base64
import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Literal

from mcp.types import CallToolResult, ImageContent, TextContent
from pydantic import BaseModel, Field

import assets as A
import workbench as wb
from agentenv_protocol import (
    AgentEnvEnvironment,
    DataPart,
    environment_card,
    extension,
    get_data,
    reset_data,
    server_instructions,
    tool,
)

PRIMITIVE_JOINTS = {"oven_004": {"door_hinge": ("oven", "door")}, "cabinet_022": {"drawer_slide": ("carcass", "drawer")}}
Material = Literal["ceramic", "porcelain", "glass", "stainless_steel", "cast_iron", "aluminum", "plastic_pp", "plastic_abs",
                   "oak", "wood", "silicone", "sheet_steel", "abs_plastic"]
Probe = Literal["tip", "rest", "drop", "cavity", "parts", "hinge", "travel", "joints"]


class Frame(BaseModel):
    summary: str


def chart(probe: dict) -> bytes | None:
    if "x" not in probe or not probe.get("y"):
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6.4, 3.4), dpi=110)
    if all(isinstance(v, str) for v in probe["x"]):
        ax.bar(range(len(probe["x"])), [v or 0 for v in probe["y"]], color="#0B7A72")
        ax.set_xticks(range(len(probe["x"])), probe["x"], rotation=35, ha="right", fontsize=7)
        ax.axhline(0.9, ls="--", color="#BF3B2E", lw=1)
    else:
        ax.plot(probe["x"], probe["y"], marker="o", ms=3.5, color="#0B7A72", lw=1.8)
    ax.set_xlabel(probe.get("x_label", ""), fontsize=9)
    ax.set_ylabel(probe.get("y_label", ""), fontsize=9)
    ax.set_title(f"{probe['kind']} probe", loc="left", fontsize=10)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


@environment_card(name="genesis-workbench")
class GenesisWorkbench(AgentEnvEnvironment):
    def __init__(self) -> None:
        super().__init__()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.pool.submit(A.gs)
        self.reset()

    async def _run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.pool, fn, *args)

    def _log(self, tool_name: str, args: dict, text: str, png: bytes | None = None) -> None:
        self.calls.append({"i": len(self.calls) + 1, "t": round(time.time() - self.t0, 1), "tool": tool_name,
                           "args": {k: v for k, v in args.items() if v is not None}, "result": text[:4000],
                           "image_png_b64": base64.b64encode(png).decode() if png else None})

    def _need(self):
        if self.obj is None:
            raise ValueError("no object is loaded")
        return self.obj

    def _only(self, *kinds):
        o = self._need()
        if o.kind not in kinds:
            raise ValueError(f"this tool doesn't apply to {o.name}, a {o.kind} object; inspect lists what you can edit")
        return o

    @server_instructions
    def instructions(self) -> str:
        return ("A physics workbench for one sim-ready object, simulated in Genesis. Start with inspect and render, run a "
                "probe that matches the complaint to see the behaviour, edit, then probe again to confirm. Units are SI: "
                "kg, m, N, seconds. Objects come in three kinds, and inspect says which: primitive objects (edit with "
                "set_inertial, set_material, set_joint), mesh objects made of parts (set_material, set_shell, set_scale, "
                "remove_part, set_inertial; source_photo shows the real object for those built from photos), and articulated URDF furniture (set_self_collision, set_joint). Keep edits "
                "physically plausible for the real object's material and size.")

    @extension("urn:genesis:load-object/v1", description="Load an object as delivered, with the contributor's complaint.")
    async def load_object(self, object: str, complaint: str = "") -> dict:
        self.reset()
        self.obj = await self._run(A.load, object)
        self.complaint = complaint
        self._before = asyncio.ensure_future(self._run(self.obj.checks, "original"))
        return {"object": object, "kind": self.obj.kind, "loaded": True}

    @reset_data
    def reset(self) -> None:
        self.obj = None
        self.complaint = ""
        self.before = None
        self._before = None
        self.calls = []
        self.last_probe = None
        self.t0 = time.time()

    @get_data
    async def episode(self) -> list[DataPart]:
        if self.obj is None:
            return [DataPart(data={"object": None})]
        if self.before is None:
            rows0, probes0, kind0 = await self._before
            self.before = {"checks": rows0, "probes": probes0, "kind": kind0}
        rows, probes, kind = await self._run(self.obj.checks, "current")
        if kind not in probes and probes:
            kind = next(iter(probes))
        b, a = self.before["probes"].get(kind, {}), probes.get(kind, {})
        text_b, text_a = self.obj.text(original=True), self.obj.text()
        data = {"object": self.obj.name, "kind": self.obj.kind, "complaint": self.complaint, "checks": rows,
                "score": round(sum(r["result"] for r in rows) / max(len(rows), 1), 3), "passed": all(r["result"] for r in rows),
                "before": {"mjcf": text_b, "checks": self.before["checks"], "probes": self.before["probes"]},
                "after": {"mjcf": text_a, "checks": rows, "probes": probes},
                "diff": self._diff(),
                "probe": {"kind": kind, "x": a.get("x"), "x_label": a.get("x_label"), "y_label": a.get("y_label"),
                          "before": b.get("y"), "after": a.get("y")},
                "calls": self.calls, "duration_s": round(time.time() - self.t0, 1)}
        return [DataPart(data=json.loads(json.dumps(data, default=float)))]

    def _diff(self) -> str:
        o = self.obj
        return (wb.diff(o.original, o.model) or "(no changes)") if o.kind == "primitive" else A.diff(o)

    @tool()
    async def inspect(self) -> str:
        """The loaded object: what it is, its parts or links with mass, centre of mass, materials and size, its joints,
        which probes apply, and the materials this workbench knows with their densities (kg/m³)."""
        o = self._need()
        out = await self._run(o.inspect)
        if o.kind == "primitive":
            out["materials"] = {k: {"density_kg_m3": v["density"], "friction": v["friction"]} for k, v in wb.MATERIALS.items()}
        out["kind"] = o.kind
        text = json.dumps(out, indent=1, default=float)
        self._log("inspect", {}, text)
        return text

    @tool()
    async def render(self) -> Annotated[CallToolResult, Frame]:
        """A rendered view of the object as it is now (a Genesis camera frame, or a schematic for primitive objects)."""
        o = self._need()
        png = await self._run(o.render, self.last_probe)
        summary = f"Rendered {o.name}"
        self._log("render", {}, summary, png)
        return CallToolResult(content=[TextContent(type="text", text=summary),
                                       ImageContent(type="image", data=base64.b64encode(png).decode(), mimeType="image/png")],
                              structuredContent={"summary": summary})

    @tool()
    def source_photo(self) -> Annotated[CallToolResult, Frame]:
        """Mesh objects built from photos: the photos of the real object the CAD was made from, with a size reference."""
        o = self._only("mesh")
        note, pngs = o.photos()
        self._log("source_photo", {}, note, pngs[0])
        return CallToolResult(content=[TextContent(type="text", text=note)] +
                              [ImageContent(type="image", data=base64.b64encode(p).decode(), mimeType="image/png") for p in pngs],
                              structuredContent={"summary": note})

    @tool()
    def set_inertial(self, link: Annotated[str, Field(description="Link name for primitive objects; 'body' for mesh objects.")] = "body",
                     mass: Annotated[float | None, Field(description="Total mass in kg.", gt=0)] = None,
                     com: Annotated[list[float] | None, Field(description="Centre of mass [x, y, z] in m.")] = None,
                     inertia: Annotated[list[float] | None, Field(description="Diagonal inertia [ixx, iyy, izz], kg·m².")] = None,
                     from_material: Annotated[Material | None, Field(description="Primitive objects: recompute from the "
                                                                                 "link's geometry at this material.")] = None) -> str:
        """Set mass properties directly. On a mesh object this overrides what its parts compute; setting only mass
        keeps the computed centre of mass and scales the inertia with it."""
        o = self._only("primitive", "mesh")
        if o.kind == "mesh":
            out = o.set_inertial(mass, com, inertia)
        else:
            m = o.model
            L = m["links"].get(link)
            if L is None or L.get("fixed"):
                raise ValueError(f"{link!r} is not a movable link of {m['name']}")
            if from_material:
                wb.apply_material(m, link, from_material)
            if mass is not None:
                L["mass"] = round(mass, 4)
            if com is not None:
                L["com"] = [round(c, 4) for c in com]
            if inertia is not None:
                L["inertia"] = inertia
            out = {"link": link, **{k: L[k] for k in ("material", "mass", "com", "inertia") if k in L}}
        text = json.dumps(out, default=float)
        self._log("set_inertial", {"link": link, "mass": mass, "com": com, "inertia": inertia, "from_material": from_material}, text)
        return text

    @tool()
    def set_material(self, part: Annotated[str, Field(description="Part name (mesh objects) or link name (primitive objects).")],
                     material: Material) -> str:
        """Give a part or link a real material: its density, and on primitive objects its friction too."""
        o = self._only("primitive", "mesh")
        if o.kind == "mesh":
            out = o.set_material(part, material)
        else:
            if part not in o.model["links"] or o.model["links"][part].get("fixed"):
                raise ValueError(f"{part!r} is not a movable link of {o.name}")
            L = wb.apply_material(o.model, part, material)
            out = {"link": part, **{k: L[k] for k in ("material", "friction", "mass", "com", "inertia") if k in L}}
        text = json.dumps(out, default=float)
        self._log("set_material", {"part": part, "material": material}, text)
        return text

    @tool()
    def set_shell(self, part: Annotated[str, Field(description="Part name, from inspect.")],
                  thickness: Annotated[float | None, Field(description="Wall thickness in m; null makes the part solid again.")]) -> str:
        """Mesh objects: treat a part as a hollow shell of this wall thickness, so its mass is its surface area times
        the thickness times its density. For a scanned part, whose surface covers both sides of the wall, half the
        area is used."""
        o = self._only("mesh")
        text = json.dumps(o.set_shell(part, thickness), default=float)
        self._log("set_shell", {"part": part, "thickness": thickness}, text)
        return text

    @tool()
    def set_scale(self, factor: Annotated[float, Field(description="Uniform scale factor applied to the delivered geometry.", gt=0)]) -> str:
        """Mesh objects: rescale the whole object uniformly from its delivered size. Mass properties follow the
        geometry, so a solid part's mass grows with the cube of the factor."""
        o = self._only("mesh")
        text = json.dumps(o.set_scale(factor), default=float)
        self._log("set_scale", {"factor": factor}, text)
        return text

    @tool()
    def remove_part(self, part: Annotated[str, Field(description="Part name, from inspect.")],
                    restore: Annotated[bool, Field(description="Put a removed part back.")] = False) -> str:
        """Mesh objects: remove a part that shouldn't be there, or put it back."""
        o = self._only("mesh")
        text = json.dumps(o.remove_part(part, restore), default=float)
        self._log("remove_part", {"part": part, "restore": restore}, text)
        return text

    @tool()
    def set_self_collision(self, enabled: bool) -> str:
        """Articulated objects: whether the object's own links collide with each other (its doors with its frame, and
        with each other). Collisions with the world are unaffected."""
        o = self._only("urdf")
        text = json.dumps(o.set_self_collision(enabled))
        self._log("set_self_collision", {"enabled": enabled}, text)
        return text

    @tool()
    def set_joint(self, name: Annotated[str, Field(description="Joint name; inspect lists them.")],
                  type: Literal["hinge", "slide"] | None = None,
                  axis: Annotated[list[float] | None, Field(description="Primitive objects: unit axis [x, y, z].")] = None,
                  range: Annotated[list[float] | None, Field(description="Primitive objects: [lower, upper], degrees or m.")] = None,
                  damping: Annotated[float | None, Field(ge=0, description="Viscous damping, N·m·s/rad or N·s/m.")] = None,
                  frictionloss: Annotated[float | None, Field(ge=0, description="Dry friction, N·m or N.")] = None) -> str:
        """Edit a joint. On primitive objects this can create a joint (give type, axis and range); on articulated
        objects only damping and friction can change."""
        o = self._only("primitive", "urdf")
        if o.kind == "urdf":
            if type is not None or axis is not None or range is not None:
                raise ValueError("on articulated objects set_joint changes only damping and frictionloss")
            out = o.set_joint(name, damping, frictionloss)
        else:
            m = o.model
            allowed = PRIMITIVE_JOINTS.get(m["name"], {})
            if name not in allowed:
                raise ValueError(f"{m['name']} has no editable joint {name!r}; editable: {list(allowed) or 'none'}")
            j = m["joints"].get(name)
            if j is None:
                if type is None or axis is None or range is None:
                    raise ValueError(f"{name} doesn't exist yet: give type, axis and range to create it")
                parent, child = allowed[name]
                j = m["joints"][name] = {"type": type, "parent": parent, "child": child, "axis": axis, "range": range,
                                         "damping": 0.0, "frictionloss": 0.0}
            for k, v in (("type", type), ("axis", axis), ("range", range), ("damping", damping), ("frictionloss", frictionloss)):
                if v is not None:
                    j[k] = v
            if len(j["axis"]) != 3 or len(j["range"]) != 2 or j["range"][0] >= j["range"][1]:
                raise ValueError("axis takes [x, y, z] and range takes [lower, upper] with lower < upper")
            out = {"joint": name, **j}
        text = json.dumps(out, default=float)
        self._log("set_joint", {"name": name, "type": type, "axis": axis, "range": range, "damping": damping,
                                "frictionloss": frictionloss}, text)
        return text

    @tool()
    async def run_probe(self, kind: Annotated[Probe, Field(description="Mesh objects: rest, drop, tip (tilt table), cavity "
                                                                        "(drop spheres into an opening), parts (gaps between "
                                                                        "parts). Primitive objects: tip, rest, hinge, travel. "
                                                                        "Articulated objects: joints.")]
                        ) -> Annotated[CallToolResult, Frame]:
        """Build a Genesis scene from the current object and measure one behaviour. Returns the numbers and a plot."""
        o = self._need()
        result = await self._run(o.probe, kind)
        self.last_probe = result
        png = await self._run(chart, result) if o.kind != "primitive" else (await self._run(wb.render, o.model, result) if "x" in result else None)
        text = json.dumps(result, default=float)
        self._log("run_probe", {"kind": kind}, text, png)
        content = [TextContent(type="text", text=text)]
        if png:
            content.append(ImageContent(type="image", data=base64.b64encode(png).decode(), mimeType="image/png"))
        return CallToolResult(content=content, structuredContent={"summary": text[:500]})

    @tool()
    def diff(self) -> str:
        """What changed between the object as delivered and now."""
        self._need()
        text = self._diff()
        self._log("diff", {}, text)
        return text


if __name__ == "__main__":
    GenesisWorkbench().serve()

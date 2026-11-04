"""The Genesis workbench env: an agent fixes a sim-ready object's physics through MCP tools, and Genesis measures it.

urn:genesis:load-object/v1 loads one object as VLM-to-CAD delivered it, bug included, and the contributor's complaint.
data/get runs the hidden acceptance probes and returns the episode: every call, the renders, the MJCF before and after,
and the probe curves."""

from __future__ import annotations

import asyncio
import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Literal

from mcp.types import CallToolResult, ImageContent, TextContent
from pydantic import BaseModel, Field

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

JOINTS = {"oven_004": {"door_hinge": ("oven", "door")}, "cabinet_022": {"drawer_slide": ("carcass", "drawer")}}
Material = Literal["ceramic", "sheet_steel", "glass", "oak", "abs_plastic"]
Probe = Literal["tip", "rest", "hinge", "travel"]


class Frame(BaseModel):
    summary: str


@environment_card(name="genesis-workbench")
class GenesisWorkbench(AgentEnvEnvironment):
    def __init__(self) -> None:
        super().__init__()
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.sim = wb.Sim()
        self.reset()

    async def _run(self, fn, *args):
        return await asyncio.get_running_loop().run_in_executor(self.pool, fn, *args)

    def _log(self, tool_name: str, args: dict, text: str, png: bytes | None = None) -> None:
        self.calls.append({"i": len(self.calls) + 1, "t": round(time.time() - self.t0, 1), "tool": tool_name,
                           "args": args, "result": text[:4000],
                           "image_png_b64": base64.b64encode(png).decode() if png else None})

    def _need(self) -> dict:
        if self.model is None:
            raise ValueError("no object is loaded")
        return self.model

    @server_instructions
    def instructions(self) -> str:
        return ("A physics workbench for one sim-ready object, simulated in Genesis. Start with inspect and render, run the "
                "probe that matches the complaint to see the behaviour, edit with set_inertial, set_material or set_joint, "
                "then run the probe again to confirm. Units are SI: kg, m, N, seconds; joint ranges are degrees for hinges "
                "and metres for slides. Keep edits physically plausible for the object's real material and size.")

    @extension("urn:genesis:load-object/v1", description="Load an object as delivered, with the contributor's complaint.")
    async def load_object(self, object: str, complaint: str = "") -> dict:
        self.reset()
        self.original = wb.initial_model(object)
        self.model = wb.clone(self.original)
        self.complaint = complaint
        rows, probes = await self._run(wb.suite, self.sim, self.original)
        self.before = {"checks": rows, "probes": probes}
        return {"object": object, "checks_failing": [r["id"] for r in rows if not r["result"]]}

    @reset_data
    def reset(self) -> None:
        self.original = None
        self.model = None
        self.complaint = ""
        self.before = None
        self.calls = []
        self.last_probe = None
        self.t0 = time.time()

    @get_data
    async def episode(self) -> list[DataPart]:
        if self.model is None:
            return [DataPart(data={"object": None})]
        rows, probes = await self._run(wb.suite, self.sim, self.model)
        kind = self.original["probe"]
        b, a = self.before["probes"][kind], probes[kind]
        data = {"object": self.model["name"], "complaint": self.complaint, "checks": rows,
                "score": round(sum(r["result"] for r in rows) / len(rows), 3), "passed": all(r["result"] for r in rows),
                "before": {"mjcf": wb.mjcf(self.original), "checks": self.before["checks"], "probes": self.before["probes"]},
                "after": {"mjcf": wb.mjcf(self.model), "checks": rows, "probes": probes},
                "diff": wb.diff(self.original, self.model),
                "probe": {"kind": kind, "x": a.get("x"), "x_label": a.get("x_label"), "y_label": a.get("y_label"),
                          "before": b.get("y"), "after": a.get("y")},
                "calls": self.calls, "duration_s": round(time.time() - self.t0, 1)}
        return [DataPart(data=data)]

    @tool()
    def inspect(self) -> str:
        """The loaded object: its links with mass, centre of mass (m, in the link frame), inertia and material; its
        joints; and the materials this workbench knows, with their densities."""
        m = self._need()
        out = {"object": m["name"], "description": m["description"], "links": m["links"], "joints": m["joints"],
               "editable_joints": list(JOINTS.get(m["name"], {})), "suggested_probe": m["probe"],
               "materials": {k: {"density_kg_m3": v["density"], "friction": v["friction"]} for k, v in wb.MATERIALS.items()}}
        text = json.dumps(out, indent=1)
        self._log("inspect", {}, text)
        return text

    @tool()
    async def render(self) -> Annotated[CallToolResult, Frame]:
        """A schematic side view of the object with its centre of mass and joints, next to the last probe's curve."""
        m = self._need()
        png = await self._run(wb.render, m, self.last_probe)
        summary = f"Rendered {m['name']}" + (f" with the last {self.last_probe['kind']} probe" if self.last_probe else "")
        self._log("render", {}, summary, png)
        return CallToolResult(content=[TextContent(type="text", text=summary),
                                       ImageContent(type="image", data=base64.b64encode(png).decode(), mimeType="image/png")],
                              structuredContent={"summary": summary})

    @tool()
    def set_inertial(self, link: Annotated[str, Field(description="Link name, from inspect.")],
                     mass: Annotated[float | None, Field(description="Mass in kg.", gt=0)] = None,
                     com: Annotated[list[float] | None, Field(description="Centre of mass [x, y, z] in m, link frame.")] = None,
                     inertia: Annotated[list[float] | None, Field(description="Diagonal inertia [ixx, iyy, izz], kg·m².")] = None,
                     from_material: Annotated[Material | None, Field(description="Recompute mass, COM and inertia from the "
                                                                                 "link's real geometry at this material's density.")] = None) -> str:
        """Set a link's mass properties directly, or recompute them from its geometry and a material."""
        m = self._need()
        L = m["links"].get(link)
        if L is None or L.get("fixed"):
            raise ValueError(f"{link!r} is not a movable link of {m['name']}")
        if from_material:
            wb.apply_material(m, link, from_material)
        if mass is not None:
            L["mass"] = round(mass, 4)
        if com is not None:
            if len(com) != 3:
                raise ValueError("com takes [x, y, z]")
            L["com"] = [round(c, 4) for c in com]
        if inertia is not None:
            if len(inertia) != 3 or min(inertia) <= 0:
                raise ValueError("inertia takes three positive values")
            L["inertia"] = inertia
        text = json.dumps({"link": link, **{k: L[k] for k in ("material", "mass", "com", "inertia") if k in L}})
        self._log("set_inertial", {"link": link, "mass": mass, "com": com, "inertia": inertia, "from_material": from_material}, text)
        return text

    @tool()
    def set_material(self, link: Annotated[str, Field(description="Link name, from inspect.")], material: Material) -> str:
        """Give a link a real material: its friction, and mass properties recomputed from its geometry."""
        m = self._need()
        if link not in m["links"] or m["links"][link].get("fixed"):
            raise ValueError(f"{link!r} is not a movable link of {m['name']}")
        L = wb.apply_material(m, link, material)
        text = json.dumps({"link": link, **{k: L[k] for k in ("material", "friction", "mass", "com", "inertia") if k in L}})
        self._log("set_material", {"link": link, "material": material}, text)
        return text

    @tool()
    def set_joint(self, name: Annotated[str, Field(description="Joint name; inspect lists the editable ones.")],
                  type: Literal["hinge", "slide"] | None = None,
                  axis: Annotated[list[float] | None, Field(description="Unit axis [x, y, z] in the parent frame.")] = None,
                  range: Annotated[list[float] | None, Field(description="[lower, upper]: degrees for a hinge, m for a slide.")] = None,
                  damping: Annotated[float | None, Field(ge=0, description="Viscous damping, N·m·s/rad or N·s/m.")] = None,
                  frictionloss: Annotated[float | None, Field(ge=0, description="Dry friction, N·m or N.")] = None) -> str:
        """Create or edit an editable joint. A new joint needs type, axis and range."""
        m = self._need()
        allowed = JOINTS.get(m["name"], {})
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
        text = json.dumps({"joint": name, **j})
        self._log("set_joint", {"name": name, "type": type, "axis": axis, "range": range, "damping": damping,
                                "frictionloss": frictionloss}, text)
        return text

    @tool()
    async def run_probe(self, kind: Annotated[Probe, Field(description="tip: tilt-table sweep for free objects; rest: "
                                                                        "flat-bench stability; hinge: release a door at 5° "
                                                                        "and watch it swing; travel: pull a drawer with 10 N.")]
                        ) -> Annotated[CallToolResult, Frame]:
        """Build a Genesis scene from the current object and measure one behaviour. Returns the numbers and a plot."""
        m = self._need()
        if kind == "tip" and m["kind"] != "rigid" or kind in ("hinge", "travel") and m["probe"] != kind:
            raise ValueError(f"the {kind} probe doesn't apply to {m['name']}; use {m['probe']} or rest")
        result = await self._run(self.sim.probe, m, kind)
        self.last_probe = result
        png = await self._run(wb.render, m, result) if "x" in result else None
        text = json.dumps(result)
        self._log("run_probe", {"kind": kind}, text, png)
        content = [TextContent(type="text", text=text)]
        if png:
            content.append(ImageContent(type="image", data=base64.b64encode(png).decode(), mimeType="image/png"))
        return CallToolResult(content=content, structuredContent={"summary": text[:500]})

    @tool()
    def diff(self) -> str:
        """The MJCF diff between the object as delivered and its current state."""
        m = self._need()
        text = wb.diff(self.original, m) or "(no changes)"
        self._log("diff", {}, text)
        return text


if __name__ == "__main__":
    GenesisWorkbench().serve()

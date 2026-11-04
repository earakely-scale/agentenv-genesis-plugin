"""The Genesis workbench env: an agent fixes a sim-ready object's physics through MCP tools, and Genesis measures it.

urn:genesis:load-object/v1 loads one object as it was delivered, bug included, with the contributor's complaint: one
of the primitive objects in workbench.py, a scanned or VLM-to-CAD mesh object, or an articulated URDF from assets/.
data/get runs the hidden acceptance checks and returns the episode: every call, the frames, the object before and after,
and the probe curves. /workbench is the contributor's page (session.py): they play with the object, chat with the agent
and approve it; urn:genesis:session-export/v1 hands the approved object, with its probe results, to save_sim_asset."""

from __future__ import annotations

import asyncio
import base64
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Annotated, Literal

from mcp.types import CallToolResult, ImageContent, TextContent
from pydantic import BaseModel, Field
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response

import assets as A
import session as S
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


chart = S.chart
EDITS = {"set_inertial", "set_material", "set_shell", "set_scale", "remove_part", "set_self_collision", "set_joint"}
PAGE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "workbench.html")


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
        if tool_name in EDITS:
            self.rev += 1
        self.calls.append({"i": len(self.calls) + 1, "t": round(time.time() - self.t0, 1), "tool": tool_name,
                           "args": {k: v for k, v in args.items() if v is not None}, "result": text[:4000],
                           "image_png_b64": base64.b64encode(png).decode() if png else None})

    def _need(self):
        if self.obj is None:
            raise ValueError("no object is loaded")
        return self.obj

    def _only(self, *kinds, edit=False):
        o = self._need()
        if o.kind not in kinds:
            raise ValueError(f"this tool doesn't apply to {o.name}, a {o.kind} object; inspect lists what you can edit")
        if edit and self.session.status != "open":
            raise ValueError(f"the contributor {self.session.status} the object, so it can't change any more")
        return o

    @server_instructions
    def instructions(self) -> str:
        return ("A physics workbench for one sim-ready object, simulated in Genesis. Start with inspect and render, run a "
                "probe that matches the complaint to see the behaviour, edit, then probe again to confirm. Units are SI: "
                "kg, m, N, seconds. Objects come in three kinds, and inspect says which: primitive objects (edit with "
                "set_inertial, set_material, set_joint), mesh objects made of parts (set_material, set_shell, set_scale, "
                "remove_part, set_inertial; source_photo shows the real object for those built from photos), and articulated URDF furniture (set_self_collision, set_joint). Keep edits "
                "physically plausible for the real object's material and size. When a contributor is testing the object in the "
                "browser, last_interaction shows what they did there and what they saw.")

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
        self.rev = 0
        self.session = S.Session()
        self._final = None

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
        s = self.session
        if s.connected:
            data["session"] = {"status": s.status, "chat": s.chat, "interactions": s.actions,
                               "decision": s.decision and {k: v for k, v in s.decision.items() if k != "model"}}
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
        o = self._only("primitive", "mesh", edit=True)
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
        o = self._only("primitive", "mesh", edit=True)
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
        o = self._only("mesh", edit=True)
        text = json.dumps(o.set_shell(part, thickness), default=float)
        self._log("set_shell", {"part": part, "thickness": thickness}, text)
        return text

    @tool()
    def set_scale(self, factor: Annotated[float, Field(description="Uniform scale factor applied to the delivered geometry.", gt=0)]) -> str:
        """Mesh objects: rescale the whole object uniformly from its delivered size. Mass properties follow the
        geometry, so a solid part's mass grows with the cube of the factor."""
        o = self._only("mesh", edit=True)
        text = json.dumps(o.set_scale(factor), default=float)
        self._log("set_scale", {"factor": factor}, text)
        return text

    @tool()
    def remove_part(self, part: Annotated[str, Field(description="Part name, from inspect.")],
                    restore: Annotated[bool, Field(description="Put a removed part back.")] = False) -> str:
        """Mesh objects: remove a part that shouldn't be there, or put it back."""
        o = self._only("mesh", edit=True)
        text = json.dumps(o.remove_part(part, restore), default=float)
        self._log("remove_part", {"part": part, "restore": restore}, text)
        return text

    @tool()
    def set_self_collision(self, enabled: bool) -> str:
        """Articulated objects: whether the object's own links collide with each other (its doors with its frame, and
        with each other). Collisions with the world are unaffected."""
        o = self._only("urdf", edit=True)
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
        o = self._only("primitive", "urdf", edit=True)
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

    @tool()
    def last_interaction(self, count: Annotated[int, Field(ge=1, le=20, description="How many of the latest actions.")] = 1
                         ) -> Annotated[CallToolResult, Frame]:
        """What the contributor did in the browser: their latest actions on the object (lift, lean, drop, fill, shake,
        open, pull, look), what each one showed them, the measured numbers, how many of your edits the object had
        then, and the image of the latest one."""
        self._need()
        recent = self.session.actions[-count:]
        if not recent:
            text = "The contributor hasn't tried anything in the workbench yet."
            self._log("last_interaction", {"count": count}, text)
            return CallToolResult(content=[TextContent(type="text", text=text)], structuredContent={"summary": text})
        rows = [{k: v for k, v in a.items() if k != "image_png_b64"} for a in recent]
        text = json.dumps({"edits_so_far": self.rev, "actions": rows}, default=float)
        content = [TextContent(type="text", text=text)]
        png = recent[-1]["image_png_b64"]
        if png:
            content.append(ImageContent(type="image", data=png, mimeType="image/png"))
        self._log("last_interaction", {"count": count}, text, base64.b64decode(png) if png else None)
        return CallToolResult(content=content, structuredContent={"summary": text[:500]})

    @extension("urn:genesis:session-export/v1", description="The contributor's decision on the object and, once they "
               "approved it, the approved object with its acceptance checks and probe results.")
    async def session_export(self) -> dict:
        s, o = self.session, self.obj
        if o is None:
            return {"status": "no object"}
        out = {"status": s.status, "object": o.name, "kind": o.kind, "label": S.label(o), "decision": None,
               "chat": s.chat, "interactions": [{k: v for k, v in a.items() if k != "image_png_b64"} for a in s.actions],
               "agent_calls": [{k: v for k, v in c.items() if k != "image_png_b64"} for c in self.calls]}
        if s.decision is None:
            return json.loads(json.dumps(out, default=float))
        out["decision"] = {k: v for k, v in s.decision.items() if k != "model"}
        if s.status == "approved":
            if self._final is None:
                self._final = asyncio.ensure_future(self._run(S.final_report, o))
            report = await self._final
            spec = {**o.spec, "edits": o.model} if o.kind == "urdf" else o.model
            text = o.mjcf(absolute=False) if o.kind == "mesh" else o.text()
            out.update({"asset": spec, "model_text": text, "diff": self._diff(),
                        "model_filename": f"{o.name}.{'edits.json' if o.kind == 'urdf' else 'xml'}", **report})
        return json.loads(json.dumps(out, default=float))

    # The contributor's page
    def create_app(self):
        app = super().create_app()
        for path, verb, handler in (("/workbench", "GET", self._page), ("/workbench/state", "GET", self._state),
                                    ("/workbench/view.png", "GET", self._view), ("/workbench/act", "POST", self._act),
                                    ("/workbench/say", "POST", self._say), ("/workbench/decide", "POST", self._decide),
                                    ("/workbench/peer/a2a", "POST", self._peer)):
            app.custom_route(path, methods=[verb])(handler)
        return app

    async def _page(self, request: Request) -> Response:
        return HTMLResponse(open(PAGE).read(), headers={"Cache-Control": "no-cache"})

    async def _state(self, request: Request) -> Response:
        o, s = self.obj, self.session
        if o is None:
            return JSONResponse({"object": None})
        return JSONResponse(json.loads(json.dumps({
            "object": o.name, "kind": o.kind, "label": S.label(o), "description": S.description(o),
            "actions": S.actions_for(o), "status": s.status, "edits": self.rev, "agent_state": s.agent_state(),
            "chat": s.chat, "interactions": [{k: v for k, v in a.items() if k not in ("image_png_b64", "result")} for a in s.actions],
            "last": s.actions[-1] if s.actions else None,
            "agent": [{"i": c["i"], "t": c["t"], "tool": c["tool"], "args": c["args"]} for c in self.calls],
            "decision": s.decision and {k: v for k, v in s.decision.items() if k != "model"}}, default=float)))

    async def _view(self, request: Request) -> Response:
        o, s = self._need(), self.session
        if s.view is None or s.view[0] != self.rev:
            rev = self.rev
            s.view = (rev, await self._run(o.render, self.last_probe))
        return Response(s.view[1], media_type="image/png", headers={"Cache-Control": "no-store"})

    async def _act(self, request: Request) -> Response:
        o, action = self._need(), (await request.json()).get("action", "")
        if self.session.status != "open":
            return JSONResponse({"error": f"the object was {self.session.status}"}, status_code=409)
        try:
            out = await self._run(S.run_action, o, action, self.last_probe)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=400)
        entry = self.session.log_action(action, out, self.rev)
        return JSONResponse(json.loads(json.dumps(entry, default=float)))

    async def _say(self, request: Request) -> Response:
        text = ((await request.json()).get("text") or "").strip()
        if not text:
            return JSONResponse({"error": "say something"}, status_code=400)
        if self.session.status != "open":
            return JSONResponse({"error": f"the object was {self.session.status}"}, status_code=409)
        return JSONResponse(self.session.say("contributor", text))

    async def _decide(self, request: Request) -> Response:
        body = await request.json()
        status = {"approve": "approved", "reject": "rejected"}.get(body.get("decision"))
        if status is None:
            return JSONResponse({"error": "decision is approve or reject"}, status_code=400)
        try:
            self.session.decide(status, (body.get("note") or "").strip(), self._need(), self.rev)
        except ValueError as e:
            return JSONResponse({"error": str(e)}, status_code=409)
        if status == "approved":
            self._final = asyncio.ensure_future(self._run(S.final_report, self.obj))
        return JSONResponse({"status": status})

    async def _peer(self, request: Request) -> Response:
        return JSONResponse(self.session.rpc(await request.json()))


if __name__ == "__main__":
    GenesisWorkbench().serve()

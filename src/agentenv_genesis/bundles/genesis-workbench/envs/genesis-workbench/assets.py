"""Real-asset objects for the workbench: scanned and VLM-to-CAD meshes as one free body of parts, and articulated URDFs.

Every object exposes the same surface to the server: inspect, edit, text (the current MJCF or config, for diffs),
probe, checks and render. Mass properties of a mesh object come from its parts: a solid part from its convex pieces at
the part's density, a shell part from its surface area times a wall thickness."""

from __future__ import annotations

import copy
import difflib
import io
import json
import math
import os
import re
import tempfile
import threading

import mujoco
import numpy as np
import trimesh

import mj
import workbench as wb

ASSETS = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets")
ENGINE = "mujoco"  # the engine that grades; Genesis gives the second opinion
DENSITY = {"ceramic": 2300.0, "porcelain": 2400.0, "glass": 2500.0, "stainless_steel": 7900.0, "cast_iron": 7200.0,
           "aluminum": 2700.0, "plastic_pp": 905.0, "plastic_abs": 1050.0, "oak": 750.0, "wood": 700.0, "silicone": 1100.0,
           "sheet_steel": 7850.0, "abs_plastic": 1050.0}
CAN = (0.033, 0.122)  # a 330 ml soda can, radius and height in m: the size reference in renders
COLORS = {"ceramic": (0.93, 0.92, 0.88), "porcelain": (0.95, 0.95, 0.95), "stainless_steel": (0.72, 0.74, 0.76),
          "plastic_abs": (0.15, 0.15, 0.16), "plastic_pp": (0.85, 0.35, 0.25), "silicone": (0.25, 0.55, 0.85)}

_lock = threading.Lock()
_gs = None


def gs():
    global _gs
    if _gs is None:
        import genesis
        if not getattr(genesis, "_initialized", False):
            genesis.init(backend=genesis.cpu if os.environ.get("GENESIS_BACKEND", "cpu") == "cpu" else genesis.gpu, seed=0,
                         logging_level="warning")
        _gs = genesis
    return _gs


def available() -> list[str]:
    return sorted(d for d in os.listdir(ASSETS) if os.path.isfile(os.path.join(ASSETS, d, "asset.json"))) if os.path.isdir(ASSETS) else []


def load(name: str):
    if name in ("mug_017", "oven_004", "cabinet_022"):
        return PrimitiveObject(name)
    spec = json.load(open(os.path.join(ASSETS, name, "asset.json")))
    return {"urdf": UrdfObject, "set": SetObject}.get(spec["kind"], MeshObject)(name, spec)


def _scene(self_collision=True, friction=1.0, n_envs=1):
    g = gs()
    s = g.Scene(sim_options=g.options.SimOptions(dt=0.002), show_viewer=False,
                rigid_options=g.options.RigidOptions(enable_self_collision=self_collision),
                vis_options=g.options.VisOptions(show_world_frame=False))
    s.add_entity(g.morphs.Plane(), material=g.materials.Rigid(friction=friction))
    return s


def _png(rgb) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.fromarray(np.asarray(rgb)).save(buf, format="PNG")
    return buf.getvalue()


def _tilt(ent):
    w, x, y, z = ent.get_quat().cpu().numpy().reshape(-1, 4).T
    return np.degrees(np.arccos(np.clip(1 - 2 * (x * x + y * y), -1, 1)))


def _rec(obj, e, k, every, **extra):
    """Record every visual geom's world pose (and anything in extra) into obj._trace, every `every` steps, for the
    browser to replay. Batched scenes record every env; finish_trace keeps one."""
    t = getattr(obj, "_trace", None)
    if t is None or k % every:
        return
    frame = [np.concatenate([v.get_pos().cpu().numpy().reshape(-1, 3), v.get_quat().cpu().numpy().reshape(-1, 4)], 1)
             for v in e.vgeoms]
    t["frames"].append(np.stack(frame, 1))
    t["t"].append(round(len(t["t"]) * every * 0.002, 3))
    for key, val in extra.items():
        t.setdefault(key, []).append(val)


def finish_trace(t: dict, env: int = 0) -> dict:
    """A recorded trace as plain lists: per frame, per visual geom, [x, y, z, qw, qx, qy, qz] (Genesis's z-up frame)."""
    out = {"t": t["t"], "frames": [np.round(f[min(env, len(f) - 1)], 5).tolist() for f in t["frames"]]}
    for key, val in t.items():
        if key not in ("t", "frames"):
            out[key] = val
    return out


def _shrink_textures(tm, size=512):
    mat = getattr(tm.visual, "material", None)
    for attr in ("image", "baseColorTexture"):
        img = getattr(mat, attr, None) if mat is not None else None
        if img is not None and max(img.size) > size:
            k = size / max(img.size)
            setattr(mat, attr, img.resize((max(1, int(img.size[0] * k)), max(1, int(img.size[1] * k)))))


def node_frames(raw: dict, nodes: list[dict]) -> dict:
    """A MuJoCo trace (each body's pose per frame) as each browser node's pose per frame: a node is drawn in its body's frame."""
    idx = [raw["bodies"].index(n["link"]) for n in nodes]
    out = {k: v for k, v in raw.items() if k not in ("bodies", "frames")}
    out["frames"] = [[f[j] for j in idx] for f in raw["frames"]]
    return out


def web_scene(obj, original=False) -> tuple[bytes, dict]:
    """The object as the browser draws it: one GLB with a node per visual mesh (named v0, v1, ...) and their poses at
    rest, with what each one is, the bounds and the centre of mass. Mesh objects and sets are drawn from their own OBJ
    files in their bodies' frames, the frames MuJoCo reports; articulated URDF objects from Genesis's visual geoms."""
    if obj.kind in ("mesh", "set"):
        return obj.web_scene(original)
    g = gs()
    with _lock:
        s = _scene()
        if obj.kind == "urdf":
            e = s.add_entity(g.morphs.URDF(file=obj.urdf, fixed=True, euler=tuple(obj.spec.get("euler", (0, 0, 0))),
                                           pos=(0, 0, obj._lift())))
        else:
            saved = obj.model
            obj.model = obj.original if original else obj.model
            try:
                e = s.add_entity(g.morphs.MJCF(file=obj._write(free=False), pos=(0, 0, obj._lift())))
                parts = [p["name"] for p in obj.model["parts"] if not p.get("removed") and p.get("visual")]
                mass, com, _ = obj.props()
                b = obj._bounds()
            finally:
                obj.model = saved
        s.build()
        scene, geoms = trimesh.Scene(), []
        for i, v in enumerate(e.vgeoms):
            tm = v.vmesh.trimesh.copy()
            _shrink_textures(tm)
            scene.add_geometry(tm, node_name=f"v{i}", geom_name=f"g{i}")
            pose = np.concatenate([v.get_pos().cpu().numpy().ravel(), v.get_quat().cpu().numpy().ravel()])
            geoms.append({"i": i, "link": v.link.name, "part": parts[i] if obj.kind == "mesh" and i < len(parts) else None,
                          "pose": np.round(pose, 5).tolist()})
        verts = np.concatenate([v.get_vverts().cpu().numpy().reshape(-1, 3) for v in e.vgeoms])
        meta = {"kind": obj.kind, "geoms": geoms, "bounds": np.round([verts.min(0), verts.max(0)], 4).tolist()}
        if obj.kind == "mesh":
            z = obj._lift()
            meta.update({"lift": round(float(z), 5), "scale": (obj.original if original else obj.model)["scale"],
                         "com": [round(float(com[0]), 4), round(float(com[1]), 4), round(float(com[2]) + z, 4)],
                         "mass_kg": round(float(mass), 4), "size_m": [round(float(v), 4) for v in b[1] - b[0]]})
        return scene.export(file_type="glb"), meta


class PrimitiveObject:
    """The original three objects, built from primitives in workbench.py."""

    kind = "primitive"

    def __init__(self, name):
        self.name = name
        self.original = wb.initial_model(name)
        self.model = wb.clone(self.original)
        self.sim = wb.Sim()

    def inspect(self):
        m = self.model
        return {"object": m["name"], "description": m["description"], "links": m["links"], "joints": m["joints"],
                "editable_joints": {"oven_004": ["door_hinge"], "cabinet_022": ["drawer_slide"]}.get(m["name"], []),
                "suggested_probe": m["probe"], "probes": ["rest", m["probe"]] if m["probe"] != "tip" else ["tip", "rest"]}

    def text(self, original=False):
        return wb.mjcf(self.original if original else self.model)

    def probe(self, kind):
        if kind == "tip" and self.model["kind"] != "rigid" or kind in ("hinge", "travel") and self.model["probe"] != kind:
            raise ValueError(f"the {kind} probe doesn't apply to {self.name}; use {self.model['probe']} or rest")
        return self.sim.probe(self.model, kind)

    def checks(self, which="current"):
        rows, probes = wb.suite(self.sim, self.original if which == "original" else self.model)
        return rows, probes, self.model["probe"]

    def render(self, last_probe=None):
        return wb.render(self.model, last_probe)


class MeshObject:
    kind = "mesh"

    def __init__(self, name, spec):
        self.name, self.dir = name, os.path.join(ASSETS, name)
        self.original = copy.deepcopy({k: v for k, v in spec.items() if k not in ("checks", "complaint", "task")})
        self.original.setdefault("override", None)
        self.model = copy.deepcopy(self.original)
        self.check_specs = spec["checks"]
        self._tmp = tempfile.mkdtemp(prefix=f"wb-{name}-")
        self._mesh_cache = {}

    # mass properties
    def _mesh(self, fname, scale):
        key = (fname, scale)
        if key not in self._mesh_cache:
            m = trimesh.load(os.path.join(self.dir, fname), force="mesh", process=False)
            m.apply_scale(scale)
            self._mesh_cache[key] = m
        return self._mesh_cache[key]

    def _part_props(self, p, s):
        if p["mode"] == "shell":
            surf = self._mesh(p["visual"], s) if p.get("visual") else trimesh.util.concatenate([self._mesh(f, s) for f in p["collision"]])
            areas = surf.area_faces
            area = float(areas.sum()) * p.get("area_factor", 1.0)
            mass = area * p["thickness"] * p["density"]
            cent = surf.triangles_center
            com = (cent * areas[:, None]).sum(0) / areas.sum()
            d = cent - com
            mk = mass * areas / areas.sum()
            inertia = sum_inertia(d, mk)
            return mass, com, inertia
        if p.get("mass_from") == "visual" and p.get("visual"):
            m = self._mesh(p["visual"], s)
            mass = abs(float(m.volume)) * p["density"]
            return mass, m.center_mass, m.moment_inertia * p["density"] * (1 if m.volume >= 0 else -1)
        mass, mcom, pieces = 0.0, np.zeros(3), []
        for f in p["collision"]:
            m = self._mesh(f, s)
            vol = abs(float(m.volume))
            mi = vol * p["density"]
            pieces.append((mi, m.center_mass, m.moment_inertia * p["density"] * (1 if m.volume >= 0 else -1)))
            mass += mi
            mcom += mi * m.center_mass
        com = mcom / max(mass, 1e-12)
        inertia = np.zeros((3, 3))
        for mi, c, I in pieces:
            dv = c - com
            inertia += I + mi * (np.dot(dv, dv) * np.eye(3) - np.outer(dv, dv))
        return mass, com, inertia

    def props(self, model=None):
        m = model or self.model
        s = m["scale"]
        tot, acc, parts = 0.0, np.zeros(3), []
        for p in m["parts"]:
            if p.get("removed"):
                continue
            mass, com, I = self._part_props(p, s)
            parts.append((mass, com, I))
            tot += mass
            acc += mass * com
        com = acc / max(tot, 1e-12)
        inertia = np.zeros((3, 3))
        for mass, c, I in parts:
            dv = c - com
            inertia += I + mass * (np.dot(dv, dv) * np.eye(3) - np.outer(dv, dv))
        o = m.get("override")
        if o:
            if o.get("mass"):
                inertia = inertia * (o["mass"] / tot)
                tot = o["mass"]
            if o.get("com"):
                com = np.array(o["com"])
            if o.get("inertia"):
                inertia = np.diag(o["inertia"])
        return tot, com, inertia

    def _bounds(self, model=None, part=None):
        m = model or self.model
        pts = []
        for p in m["parts"]:
            if p.get("removed") or (part and p["name"] != part):
                continue
            for f in p["collision"]:
                pts.append(self._mesh(f, m["scale"]).bounds)
        b = np.array(pts)
        return np.array([b[:, 0, :].min(0), b[:, 1, :].max(0)])

    # MJCF
    def mjcf(self, model=None, free=True, absolute=True):
        m = model or self.model
        mass, com, I = self.props(m)
        root = self.dir if absolute else "."
        s = m["scale"]
        assets, geoms, tex = [], [], ""
        for p in m["parts"]:
            if p.get("removed"):
                continue
            for f in p["collision"]:
                mid = re.sub(r"\W", "_", f)
                assets.append(f'<mesh name="{mid}" file="{os.path.join(root, f)}" scale="{s:g} {s:g} {s:g}"/>')
                geoms.append(f'<geom type="mesh" mesh="{mid}" group="3" rgba="1 1 1 0"/>')
            if p.get("visual"):
                vid = "vis_" + re.sub(r"\W", "_", p["name"])
                assets.append(f'<mesh name="{vid}" file="{os.path.join(root, p["visual"])}" scale="{s:g} {s:g} {s:g}"/>')
                if p.get("texture"):
                    assets.append(f'<texture type="2d" name="tex_{vid}" file="{os.path.join(root, p["texture"])}"/>'
                                  f'<material name="mat_{vid}" texture="tex_{vid}"/>')
                    geoms.append(f'<geom type="mesh" mesh="{vid}" material="mat_{vid}" contype="0" conaffinity="0" group="2"/>')
                else:
                    r, g_, b = COLORS.get(p["material"], (0.8, 0.8, 0.8))
                    geoms.append(f'<geom type="mesh" mesh="{vid}" rgba="{r:.2f} {g_:.2f} {b:.2f} 1" contype="0" conaffinity="0" group="2"/>')
        full = f"{I[0, 0]:.4e} {I[1, 1]:.4e} {I[2, 2]:.4e} {I[0, 1]:.4e} {I[0, 2]:.4e} {I[1, 2]:.4e}"
        joint = "<freejoint/>" if free else ""
        return (f'<mujoco model="{self.name}">\n  <asset>\n    ' + "\n    ".join(assets) + "\n  </asset>\n  <worldbody>\n"
                f'    <body name="body">{joint}\n      <inertial pos="{com[0]:.4f} {com[1]:.4f} {com[2]:.4f}" mass="{mass:.4f}" fullinertia="{full}"/>\n      '
                + "\n      ".join(geoms) + "\n    </body>\n  </worldbody>\n</mujoco>\n")

    def text(self, original=False):
        m = self.original if original else self.model
        mass, com, _ = self.props(m)
        head = json.dumps({"scale": m["scale"], "override": m.get("override"),
                           "parts": [{k: p.get(k) for k in ("name", "material", "density", "mode", "thickness", "removed")}
                                     for p in m["parts"]]}, indent=1)
        inertial = re.search(r"<inertial[^>]*/>", self.mjcf(m, absolute=False)).group(0)
        return head + "\n" + inertial + "\n"

    def _write(self, free=True):
        path = os.path.join(self._tmp, f"{self.name}{'' if free else '_fixed'}.xml")
        with open(path, "w") as f:
            f.write(self.mjcf(free=free))
        return path

    def photos(self):
        names = self.model.get("photos") or []
        if not names:
            raise ValueError(f"{self.name} has no source photos")
        return self.model.get("photo_note", ""), [open(os.path.join(self.dir, n), "rb").read() for n in names]

    # tools
    def inspect(self):
        m = self.model
        mass, com, I = self.props()
        b = self._bounds()
        parts = []
        for p in m["parts"]:
            pm, pc, _ = self._part_props(p, m["scale"]) if not p.get("removed") else (0.0, np.zeros(3), None)
            pb = self._bounds(part=p["name"]) if not p.get("removed") else None
            parts.append({"name": p["name"], "material": p["material"], "density_kg_m3": p["density"], "mode": p["mode"],
                          "thickness_m": p.get("thickness"), "removed": bool(p.get("removed")), "mass_kg": round(pm, 4),
                          "bbox_m": None if pb is None else [round(float(v), 4) for v in pb[1] - pb[0]],
                          "convex_pieces": len(p["collision"])})
        return {"object": self.name, "description": m["description"], "label": m["label"], "source": m.get("credit"),
                "scale": m["scale"], "bbox_m": [round(float(v), 4) for v in b[1] - b[0]],
                "mass_kg": round(mass, 4), "com_m": [round(float(v), 4) for v in com],
                "inertia_diag_kg_m2": [round(float(v), 6) for v in np.diag(I)], "override": m.get("override"),
                "parts": parts, "probes": ["rest", "drop", "tip", "cavity", "parts"],
                "has_source_photos": bool(m.get("photos")),
                "materials": {k: v for k, v in DENSITY.items()}}

    def part(self, name):
        for p in self.model["parts"]:
            if p["name"] == name:
                return p
        raise ValueError(f"{self.name} has no part {name!r}; parts: {[p['name'] for p in self.model['parts']]}")

    def set_material(self, part, material):
        if material not in DENSITY:
            raise ValueError(f"unknown material {material!r}; known: {sorted(DENSITY)}")
        p = self.part(part)
        p["material"], p["density"] = material, DENSITY[material]
        return self._recompute()

    def set_shell(self, part, thickness):
        p = self.part(part)
        if thickness is None:
            p["mode"], p["thickness"] = "solid", None
        else:
            if not 0.0002 <= thickness <= 0.05:
                raise ValueError("thickness must be between 0.2 mm and 50 mm")
            p["mode"], p["thickness"] = "shell", float(thickness)
        return self._recompute()

    def set_scale(self, factor):
        if not 0.1 <= factor <= 10:
            raise ValueError("scale must be between 0.1 and 10")
        self.model["scale"] = round(float(factor), 4)
        return self._recompute()

    def remove_part(self, part, restore=False):
        p = self.part(part)
        if not restore and sum(not q.get("removed") for q in self.model["parts"]) <= 1:
            raise ValueError("can't remove the last part")
        p["removed"] = not restore
        return self._recompute()

    def set_inertial(self, mass=None, com=None, inertia=None):
        o = self.model.get("override") or {}
        if mass is not None:
            o["mass"] = round(float(mass), 4)
        if com is not None:
            o["com"] = [round(float(c), 4) for c in com]
        if inertia is not None:
            o["inertia"] = [float(v) for v in inertia]
        self.model["override"] = o or None
        return self.summary()

    def _recompute(self):
        """Editing geometry or materials replaces a declared inertial with what the parts compute."""
        cleared = self.model.get("override") is not None
        self.model["override"] = None
        out = self.summary()
        if cleared:
            out["note"] = "the object's declared inertial was replaced by mass properties computed from its parts"
        return out

    def summary(self):
        mass, com, _ = self.props()
        b = self._bounds()
        return {"mass_kg": round(mass, 4), "com_m": [round(float(v), 4) for v in com],
                "bbox_m": [round(float(v), 4) for v in b[1] - b[0]], "scale": self.model["scale"]}

    # probes
    def probe(self, kind, engine=ENGINE):
        if kind == "parts":
            return self._parts()
        if engine == "genesis":
            with _lock:
                return {"rest": self._rest, "drop": self._drop, "tip": self._tip, "cavity": self._cavity}.get(kind, self._bad(kind))()
        traced = getattr(self, "_trace", None) is not None
        b = self._bounds()
        if kind == "rest":
            out, tr = mj.rest(self.mjcf(), self._lift(), traced)
        elif kind == "drop":
            out, tr = mj.drop(self.mjcf(), self._lift(), float(b[1][2] - b[0][2]), traced)
        elif kind == "tip":
            out, tr = mj.tip(self.mjcf(), self._lift(), traced)
        elif kind == "cavity":
            out, tr = mj.cavity(self.mjcf(free=False), self._lift(), b, traced)
        else:
            return self._bad(kind)()
        if traced:
            self._trace = tr
        return out

    def web_scene(self, original=False):
        saved = self.model
        self.model = self.original if original else self.model
        try:
            s, z = self.model["scale"], self._lift()
            mass, com, _ = self.props()
            b = self._bounds()
            scene, geoms = trimesh.Scene(), []
            for i, n in enumerate(self.visual_nodes()):
                p = self.part(n["part"])
                tm = trimesh.load(os.path.join(self.dir, p["visual"]), force="mesh", process=False).copy()
                tm.apply_scale(s)
                if not isinstance(tm.visual, trimesh.visual.TextureVisuals):
                    r, g_, bb = COLORS.get(p["material"], (0.8, 0.8, 0.8))
                    tm.visual = trimesh.visual.ColorVisuals(tm, face_colors=[int(r * 255), int(g_ * 255), int(bb * 255), 255])
                _shrink_textures(tm)
                scene.add_geometry(tm, node_name=f"v{i}", geom_name=f"g{i}")
                geoms.append({"i": i, "link": "body", "part": p["name"], "pose": [0.0, 0.0, round(z, 5), 1.0, 0.0, 0.0, 0.0]})
            meta = {"kind": "mesh", "engine": ENGINE, "geoms": geoms, "lift": round(z, 5), "scale": s,
                    "bounds": [[round(float(b[0][0]), 4), round(float(b[0][1]), 4), round(float(b[0][2]) + z, 4)],
                               [round(float(b[1][0]), 4), round(float(b[1][1]), 4), round(float(b[1][2]) + z, 4)]],
                    "com": [round(float(com[0]), 4), round(float(com[1]), 4), round(float(com[2]) + z, 4)],
                    "mass_kg": round(float(mass), 4), "size_m": [round(float(v), 4) for v in b[1] - b[0]]}
            return scene.export(file_type="glb"), meta
        finally:
            self.model = saved

    def visual_nodes(self):
        """The browser scene's nodes: one per visual part, each in the body's frame."""
        return [{"part": p["name"], "link": "body"} for p in self.model["parts"] if not p.get("removed") and p.get("visual")]

    def _bad(self, kind):
        def f():
            raise ValueError(f"unknown probe {kind!r} for {self.name}; use rest, drop, tip, cavity or parts")
        return f

    def _lift(self):
        return -float(self._bounds()[0][2]) + 0.0005

    def _rest(self):
        g = gs()
        s = _scene()
        e = s.add_entity(g.morphs.MJCF(file=self._write(), pos=(0, 0, self._lift())))
        s.build()
        for k in range(600):
            s.step()
            _rec(self, e, k, 10)
        v = float(np.abs(e.get_dofs_velocity().cpu().numpy()).max())
        t = float(_tilt(e)[0])
        return {"kind": "rest", "max_speed": round(v, 4), "tilt_deg": round(t, 2), "stable": bool(v < 0.02 and t < 3)}

    def _drop(self):
        g = gs()
        h = float(self._bounds()[1][2] - self._bounds()[0][2])
        s = _scene()
        e = s.add_entity(g.morphs.MJCF(file=self._write(), pos=(0, 0, self._lift() + 2 * h)))
        s.build()
        settle, pen, ts, zs = None, 0.0, [], []
        for k in range(1200):
            s.step()
            _rec(self, e, k, 8)
            if k % 25 == 0:
                z0 = float(e.get_AABB().cpu().numpy().reshape(2, 3)[0][2])
                pen = max(pen, -z0)
                ts.append(round(k * 0.002, 2))
                zs.append(round(z0 * 100, 2))
                if settle is None and k > 50 and float(np.abs(e.get_dofs_velocity().cpu().numpy()).max()) < 0.02:
                    settle = round(k * 0.002, 2)
        return {"kind": "drop", "x": ts, "x_label": "time (s)", "y_label": "lowest point (cm)", "y": zs,
                "settles_s": settle, "max_penetration_mm": round(pen * 1000, 1), "final_tilt_deg": round(float(_tilt(e)[0]), 1)}

    def _tip(self):
        import torch
        g = gs()
        leans = list(range(10, 75, 5))
        s = _scene(n_envs=len(leans))
        e = s.add_entity(g.morphs.MJCF(file=self._write(), pos=(0, 0, self._lift())))
        s.build(n_envs=len(leans))
        p0 = e.get_pos().cpu().numpy().reshape(-1, 3).copy()
        for k in range(900):
            f = min(1.0, k / 400)
            gv = [[9.81 * math.sin(math.radians(a) * f), 0.0, -9.81 * math.cos(math.radians(a) * f)] for a in leans]
            s.rigid_solver.set_gravity(torch.tensor(gv, dtype=torch.float32))
            s.step()
            _rec(self, e, k, 10, ramp=round(f, 4))
        tilt = _tilt(e)
        slide = np.linalg.norm((e.get_pos().cpu().numpy().reshape(-1, 3) - p0)[:, :2], axis=1)
        mode = ["topple" if t > 30 else "slide" if d > 0.03 else "holds" for t, d in zip(tilt, slide)]
        first = next(((a, m) for a, m in zip(leans, mode) if m != "holds"), (None, "holds"))
        return {"kind": "tip", "x": leans, "x_label": "tilt-table angle (deg)", "y_label": "final tilt (deg)",
                "y": [round(float(t), 1) for t in tilt], "modes": mode, "first_failure_deg": first[0], "mode": first[1],
                "note": "Friction 1.0 on the table. 'slide' means it slid more than 3 cm before tipping."}

    def _cavity(self):
        g = gs()
        b = self._bounds()
        top = float(b[1][2] - b[0][2])
        cx, cy = [float(v) for v in (b[0][:2] + b[1][:2]) / 2]
        s = _scene()
        e = s.add_entity(g.morphs.MJCF(file=self._write(free=False), pos=(0, 0, self._lift())))
        balls = [s.add_entity(g.morphs.Sphere(radius=0.008, pos=(cx + 0.004 * (i - 2), cy + 0.003 * (i % 2), top + 0.03 + 0.025 * i)),
                              material=g.materials.Rigid(rho=1000)) for i in range(5)]
        s.build()
        for k in range(1000):
            s.step()
            if getattr(self, "_trace", None) is not None and k % 10 == 0:
                _rec(self, e, k, 10, balls=[np.round(x.get_pos().cpu().numpy().ravel(), 4).tolist() for x in balls])
        zs = [float(x.get_pos().cpu().numpy().ravel()[2]) for x in balls]
        inside = sum(1 for z in zs if z < top - 0.006)
        return {"kind": "cavity", "rim_height_cm": round(top * 100, 1), "ball_heights_cm": [round(z * 100, 1) for z in zs],
                "balls_inside": inside, "open": bool(inside >= 3),
                "note": "Five 8 mm spheres dropped into the opening of the object held in place."}

    def _parts(self):
        from scipy.spatial import cKDTree
        live = [p for p in self.model["parts"] if not p.get("removed")]
        s = self.model["scale"]
        pts = {}
        for p in live:
            m = self._mesh(p["visual"], s) if p.get("visual") else trimesh.util.concatenate([self._mesh(f, s) for f in p["collision"]])
            v = m.vertices
            if len(v) > 4000:
                v = trimesh.sample.sample_surface(m, 4000, seed=0)[0]
            pts[p["name"]] = np.asarray(v)
        gaps = {}
        for a in pts:
            others = [pts[b] for b in pts if b != a]
            if not others:
                gaps[a] = None
                continue
            d, _ = cKDTree(np.vstack(others)).query(pts[a])
            gaps[a] = round(float(d.min()) * 1000, 1)
        floating = [k for k, v in gaps.items() if v is not None and v > 2.0]
        return {"kind": "parts", "gap_to_nearest_part_mm": gaps, "floating": floating,
                "note": "Closest distance from each part's surface to any other part's surface; over 2 mm counts as floating."}

    # checks
    def checks(self, which="current", engine=ENGINE):
        saved = self.model
        if which == "original":
            self.model = self.original
        try:
            mass, _, _ = self.props()
            b = self._bounds()
            ext = b[1] - b[0]
            probes, rows = {}, []
            for c in self.check_specs:
                t = c["type"]
                if t == "mass_band":
                    rows.append({"id": "mass", "result": bool(c["lo"] <= mass <= c["hi"]),
                                 "description": f"mass within {c['lo']}-{c['hi']} kg for a real {self.model['label']}; got {mass:.3f} kg"})
                elif t == "height_band":
                    rows.append({"id": "height", "result": bool(c["lo"] <= ext[2] <= c["hi"]),
                                 "description": f"height within {c['lo'] * 100:.1f}-{c['hi'] * 100:.1f} cm; got {ext[2] * 100:.1f} cm"})
                elif t == "width_band":
                    w = float(max(ext[0], ext[1]))
                    rows.append({"id": "width", "result": bool(c["lo"] <= w <= c["hi"]),
                                 "description": f"width within {c['lo'] * 100:.0f}-{c['hi'] * 100:.0f} cm; got {w * 100:.1f} cm"})
                elif t == "cavity_open":
                    probes["cavity"] = r = self.probe("cavity", engine)
                    rows.append({"id": "cavity-open", "result": bool(r["open"]),
                                 "description": f"the opening holds dropped objects; {r['balls_inside']} of 5 spheres inside"})
                elif t == "rests_upright":
                    probes["rest"] = r = self.probe("rest", engine)
                    rows.append({"id": "rests-upright", "result": bool(r["stable"]),
                                 "description": f"rests upright on a flat bench; tilt {r['tilt_deg']} deg, speed {r['max_speed']}"})
                elif t == "tilt_min_deg":
                    probes["tip"] = r = self.probe("tip", engine)
                    ok = r["first_failure_deg"] is None or r["first_failure_deg"] >= c["value"]
                    rows.append({"id": "not-top-heavy", "result": bool(ok),
                                 "description": f"stays put on a tilt table up to {c['value']} deg; first {r['mode']} at {r['first_failure_deg']}"})
                elif t == "no_floating_parts":
                    probes["parts"] = r = self.probe("parts", engine)
                    rows.append({"id": "no-floating-parts", "result": not r["floating"],
                                 "description": f"every part touches another; floating: {r['floating'] or 'none'}"})
            return rows, probes, next(iter(probes), "rest")
        finally:
            self.model = saved

    def render(self, last_probe=None):
        """The object on the floor beside a 330 ml can, so its size shows in the picture."""
        g = gs()
        b = self._bounds()
        r, h = CAN
        cx = float(b[1][0]) + 0.04 + r
        lo, hi = np.minimum(b[0], [cx - r, -r, 0.0]), np.maximum(b[1], [cx + r, r, h])
        ctr, ext = (lo + hi) / 2, hi - lo
        size = float(max(ext))
        with _lock:
            s = _scene()
            s.add_entity(g.morphs.MJCF(file=self._write(free=False), pos=(0, 0, self._lift())))
            s.add_entity(g.morphs.Cylinder(radius=r, height=h, pos=(cx, 0.0, h / 2 + 0.001), fixed=True),
                         surface=g.surfaces.Default(color=(0.78, 0.13, 0.11)))
            cam = s.add_camera(res=(640, 480), pos=(float(ctr[0]) + 1.3 * size, float(ctr[1]) - 1.9 * size, 1.25 * size),
                               lookat=(float(ctr[0]), float(ctr[1]), 0.4 * float(ext[2])), fov=35, GUI=False)
            s.build()
            return _png(cam.render(rgb=True)[0])


class SetObject:
    """A set of free bodies that fit together, like a knife block and its knives: an MJCF of bodies, each with a
    material per part, a designed pose, and collision as one convex hull per part. MuJoCo collides any mesh as its
    convex hull, so a body whose whole mesh was given as one collision geom (collision "hull") can't enter a slot."""

    kind = "set"

    def __init__(self, name, spec):
        self.name, self.dir, self.spec = name, os.path.join(ASSETS, name), spec
        self.check_specs = spec["checks"]
        self.original = {"collision": dict(spec.get("collision") or {}), "materials": {}, "inertial": {}}
        self.model = copy.deepcopy(self.original)
        self.block = spec["bodies"][0]["name"]
        self.axis = spec["slots"][next(iter(spec["slots"]))]["axis_out"]
        self.seats = {b["name"]: b["pose"] for b in spec["bodies"][1:]}

    def body(self, name):
        for b in self.spec["bodies"]:
            if b["name"] == name:
                return b
        raise ValueError(f"no body {name!r}; bodies: {[b['name'] for b in self.spec['bodies']]}")

    def _material(self, model, body, part):
        return model["materials"].get(f"{body['name']}.{part['name']}", part["material"])

    def _inertial(self, model, body):
        out = dict(body["inertial"])
        out.update(model["inertial"].get(body["name"], {}))
        return out

    def mjcf(self, model=None, free=True, absolute=True, bodies=None, meshdir=None):
        m = model or self.model
        mats = self.spec["materials"]
        assets, worlds = [], []
        for b in self.spec["bodies"]:
            if bodies is not None and b["name"] not in bodies:
                continue
            geoms = []
            hull = m["collision"].get(b["name"]) == "hull"
            if hull:
                mid = b["hull"].removesuffix(".obj")
                assets.append(f'<mesh name="{mid}" file="{b["hull"]}"/>')
                fr = mats[self._material(m, b, b["parts"][0])]["friction"]
                geoms.append(f'<geom type="mesh" mesh="{mid}" friction="{fr:g} 0.005 0.0001" group="3" rgba="1 1 1 0"/>')
            for part in b["parts"]:
                mat = mats[self._material(m, b, part)]
                if not hull:
                    for f in part["collision"]:
                        mid = f.removesuffix(".obj")
                        assets.append(f'<mesh name="{mid}" file="{f}"/>')
                        geoms.append(f'<geom type="mesh" mesh="{mid}" friction="{mat["friction"]:g} 0.005 0.0001" group="3" rgba="1 1 1 0"/>')
                vid = "vis_" + part["visual"].removesuffix(".obj")
                assets.append(f'<mesh name="{vid}" file="{part["visual"]}"/>')
                r, g_, bb, a = mat.get("rgba", (0.8, 0.8, 0.8, 1))
                geoms.append(f'<geom type="mesh" mesh="{vid}" contype="0" conaffinity="0" group="2" rgba="{r:.3f} {g_:.3f} {bb:.3f} {a:g}"/>')
            i = self._inertial(m, b)
            pose = b["pose"]
            worlds.append(f'<body name="{b["name"]}" pos="{pose[0]:.6f} {pose[1]:.6f} {pose[2]:.6f}" quat="{" ".join(f"{v:.6f}" for v in pose[3:])}">'
                          + ("<freejoint/>" if free else "")
                          + f'<inertial pos="{" ".join(f"{v:.6f}" for v in i["com"])}" mass="{i["mass"]:.6f}" fullinertia="{" ".join(f"{v:.6e}" for v in i["fullinertia"])}"/>'
                          + "".join(geoms) + "</body>")
        root = meshdir if meshdir is not None else (self.dir if absolute else ".")
        return (f'<mujoco model="{self.name}">\n  <compiler meshdir="{root}"/>\n  <asset>\n    ' + "\n    ".join(dict.fromkeys(assets))
                + "\n  </asset>\n  <worldbody>\n    " + "\n    ".join(worlds) + "\n  </worldbody>\n</mujoco>\n")

    def _lift(self):
        return 0.0

    def text(self, original=False):
        return json.dumps(self.original if original else self.model, indent=1, sort_keys=True) + "\n"

    def summary(self):
        mass = sum(self._inertial(self.model, b)["mass"] for b in self.spec["bodies"])
        return {"mass_kg": round(mass, 4), "bodies": len(self.spec["bodies"])}

    def visual_nodes(self):
        return [{"part": f"{b['name']}.{p['name']}", "link": b["name"]} for b in self.spec["bodies"] for p in b["parts"]]

    def web_scene(self, original=False):
        m = self.original if original else self.model
        scene, geoms, verts = trimesh.Scene(), [], []
        i = 0
        for b in self.spec["bodies"]:
            for part in b["parts"]:
                tm = trimesh.load(os.path.join(self.dir, part["visual"]), force="mesh", process=False).copy()
                r, g_, bb, a = self.spec["materials"][self._material(m, b, part)].get("rgba", (0.8, 0.8, 0.8, 1))
                tm.visual = trimesh.visual.ColorVisuals(tm, face_colors=[int(r * 255), int(g_ * 255), int(bb * 255), int(a * 255)])
                scene.add_geometry(tm, node_name=f"v{i}", geom_name=f"g{i}")
                geoms.append({"i": i, "link": b["name"], "part": f"{b['name']}.{part['name']}", "pose": b["pose"]})
                R = trimesh.transformations.quaternion_matrix(b["pose"][3:])[:3, :3]
                verts.append(tm.vertices @ R.T + np.asarray(b["pose"][:3]))
                i += 1
        v = np.concatenate(verts)
        mass = sum(self._inertial(m, b)["mass"] for b in self.spec["bodies"])
        meta = {"kind": "set", "engine": ENGINE, "geoms": geoms, "lift": 0.0, "scale": 1.0, "mass_kg": round(mass, 4),
                "bounds": np.round([v.min(0), v.max(0)], 4).tolist(), "size_m": np.round(v.max(0) - v.min(0), 4).tolist()}
        return scene.export(file_type="glb"), meta

    # probes
    def probe(self, kind, engine=ENGINE):
        if engine == "genesis":
            return self._genesis(kind)
        traced = getattr(self, "_trace", None) is not None
        doc = self.mjcf()
        fn = {"rest": lambda: mj.set_rest(doc, self.block, self.seats, self.axis, traced),
              "seat": lambda: mj.set_seat(doc, self.block, self.seats, self.axis, trace=traced),
              "extract": lambda: mj.set_extract(doc, self.block, self.seats, self.axis, trace=traced),
              "tip": lambda: mj.set_tip(doc, self.block, self.seats, traced)}.get(kind)
        if fn is None:
            raise ValueError(f"{self.name} takes the rest, seat, extract and tip probes")
        out, tr = fn()
        out["labels"] = {b["name"]: b.get("label", b["name"]) for b in self.spec["bodies"][1:]}
        if traced:
            self._trace = tr
        return out

    def _genesis(self, kind):
        """A second opinion in Genesis: every body as its own entity at its designed pose, settled for 1.5 s."""
        if kind != "rest":
            raise ValueError("Genesis gives a second opinion on the rest probe of a set")
        g = gs()
        with _lock:
            s = _scene()
            ents = {}
            for b in self.spec["bodies"]:
                path = os.path.join(tempfile.mkdtemp(prefix="wbset-"), f"{b['name']}.xml")
                doc = self.mjcf(bodies=[b["name"]])
                pose = b["pose"]
                doc = re.sub(r'<body name="([^"]+)" pos="[^"]*" quat="[^"]*">', r'<body name="\1">', doc)
                open(path, "w").write(doc)
                ents[b["name"]] = s.add_entity(g.morphs.MJCF(file=path, pos=tuple(pose[:3]), quat=tuple(pose[3:])))
            s.build()
            for _ in range(750):
                s.step()
            blk = ents[self.block]
            bp, bq = blk.get_pos().cpu().numpy().ravel(), blk.get_quat().cpu().numpy().ravel()
            knives = {}
            for name, seat in self.seats.items():
                e = ents[name]
                p = np.concatenate([e.get_pos().cpu().numpy().ravel(), e.get_quat().cpu().numpy().ravel()])
                rel_p, rel_q = mj._relative(np.concatenate([bp, bq]), p)
                dp = rel_p - np.asarray(seat[:3])
                knives[name] = {"shift_mm": round(float(np.linalg.norm(dp)) * 1000, 2), "out_of_slot_mm": round(float(dp @ np.asarray(self.axis)) * 1000, 2),
                                "rotation_deg": round(mj.rotation_deg(rel_q, np.asarray(seat[3:])), 2)}
            return {"kind": "rest", "engine": "genesis", "knives": knives, "block_tilt_deg": round(float(_tilt(blk)[0]), 2)}

    def checks(self, which="current", engine=ENGINE):
        saved = self.model
        if which == "original":
            self.model = self.original
        try:
            rows, probes = [], {}
            for c in self.check_specs:
                t = c["type"]
                if t == "set_seated":
                    probes["rest"] = r = self.probe("rest", engine)
                    bad = [n for n, k in r["knives"].items() if abs(k.get("out_of_slot_mm", k["shift_mm"])) > c["mm"] or k["rotation_deg"] > c["deg"]]
                    rows.append({"id": "knives-seated", "result": not bad,
                                 "description": f"every knife rests within {c['mm']:g} mm of its seat's depth and {c['deg']:g} deg of its angle; off: {bad or 'none'}"})
                elif t == "set_inserts" and engine == "mujoco":
                    probes["seat"] = r = self.probe("seat", engine)
                    bad = [n for n, k in r["knives"].items() if abs(k["out_of_slot_mm"]) > c["mm"]]
                    rows.append({"id": "knives-insert", "result": not bad,
                                 "description": f"every knife dropped 2 cm above its slot goes all the way in, within {c['mm']:g} mm; not: {bad or 'none'}"})
                elif t == "set_pull_free" and engine == "mujoco":
                    probes["extract"] = r = self.probe("extract", engine)
                    bad = [n for n, k in r["knives"].items() if k["freed_at_weight_ratio"] is None
                           or k["freed_at_weight_ratio"] > c["max_weight_ratio"] or k["block_lift_mm"] > c["max_block_lift_mm"]]
                    rows.append({"id": "knives-pull-free", "result": not bad,
                                 "description": f"every knife pulls free under {c['max_weight_ratio']:g}x its weight without lifting the block "
                                                f"{c['max_block_lift_mm']:g} mm; not: {bad or 'none'}"})
                elif t == "set_tilt_min_deg" and engine == "mujoco":
                    probes["tip"] = r = self.probe("tip", engine)
                    ok = r["first_failure_deg"] is None or r["first_failure_deg"] >= c["value"]
                    rows.append({"id": "set-stable", "result": bool(ok),
                                 "description": f"the set stays put on a tilt table to {c['value']} deg; first: {r['mode']} at {r['first_failure_deg']}"})
                elif t == "set_mass_band":
                    mass = self.summary()["mass_kg"]
                    rows.append({"id": "set-mass", "result": bool(c["lo"] <= mass <= c["hi"]),
                                 "description": f"the set weighs {c['lo']}-{c['hi']} kg; got {mass:.3f} kg"})
            return rows, probes, "seat"
        finally:
            self.model = saved

    # tools
    def inspect(self):
        m = self.model
        bodies = []
        for b in self.spec["bodies"]:
            row = {"name": b["name"], "label": b.get("label"), "inertial": self._inertial(m, b),
                   "collision": "one convex hull of the whole body" if m["collision"].get(b["name"]) == "hull" else "a convex hull per part",
                   "parts": [{"name": p["name"], "material": self._material(m, b, p), "convex_pieces": len(p["collision"])} for p in b["parts"]]}
            if b.get("slot"):
                sl = self.spec["slots"][b["slot"]]
                blade = trimesh.load(os.path.join(self.dir, next(p for p in b["parts"] if p["name"] == "blade")["collision"][0]), force="mesh")
                hull = trimesh.load(os.path.join(self.dir, b["hull"]), force="mesh")
                row["slot"] = {"width_mm": round(sl["width"] * 1000, 2), "length_mm": round(sl["length"] * 1000, 1), "depth_mm": round(sl["depth"] * 1000, 1)}
                row["collision_thickness_mm"] = {"blade_hull": round(float(np.ptp(blade.vertices[:, 2])) * 1000, 2),
                                                 "whole_knife_hull": round(float(np.ptp(hull.vertices[:, 2])) * 1000, 2)}
            bodies.append(row)
        return {"object": self.name, "description": self.spec["description"], "label": self.spec["label"], "source": self.spec.get("credit"),
                "bodies": bodies, "materials": {k: {kk: v[kk] for kk in ("density", "friction")} for k, v in self.spec["materials"].items()},
                "probes": ["rest", "seat", "extract", "tip"], "engine": ENGINE,
                "note": "MuJoCo collides every mesh geom as its convex hull. Poses are [x, y, z, qw, qx, qy, qz] in metres, z up."}

    def stability(self):
        """The set's static margin: how far it can tilt toward each side before its centre of mass passes the edge of
        what it stands on (its pads' footprint), at its designed poses."""
        m = mj.compile(self.mjcf())
        d = mujoco.MjData(m)
        mujoco.mj_forward(m, d)
        total = float(m.body_subtreemass[0])
        com = d.subtree_com[0].copy()
        pts = []
        for g in range(m.ngeom):
            if m.geom_contype[g] and m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
                k = m.geom_dataid[g]
                v = m.mesh_vert[m.mesh_vertadr[k]:m.mesh_vertadr[k] + m.mesh_vertnum[k]] @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g]
                pts.append(v[v[:, 2] < v[:, 2].min() + 0.0005][:, :2] if v[:, 2].min() < 0.002 else np.zeros((0, 2)))
        from scipy.spatial import ConvexHull
        foot = np.concatenate(pts)
        hull = ConvexHull(foot)
        out = {}
        for side, n in (("back", (0, 1)), ("front", (0, -1)), ("left", (-1, 0)), ("right", (1, 0))):
            edge = max(float(np.dot(p, n)) for p in foot[hull.vertices])
            out[side] = round(math.degrees(math.atan2(edge - float(np.dot(com[:2], n)), float(com[2]))), 1)
        return {"mass_kg": round(total, 4), "com_m": np.round(com, 4).tolist(), "tip_angle_deg": out,
                "footprint_m": np.round(foot[hull.vertices], 4).tolist(), "least_stable": min(out, key=out.get)}

    def set_collision(self, body, mode):
        b = self.body(body)
        if mode not in ("parts", "hull"):
            raise ValueError("mode is 'parts' (a convex hull per part) or 'hull' (one convex hull of the whole body)")
        if mode == "hull" and not b.get("hull"):
            raise ValueError(f"{body} has no whole-body hull")
        self.model["collision"][body] = mode
        return {"body": body, "collision": mode}

    def set_material(self, part, material):
        body, _, pname = part.partition(".")
        b = self.body(body)
        if not any(p["name"] == pname for p in b["parts"]):
            raise ValueError(f"{body} has no part {pname!r}; give a part as body.part, e.g. {body}.{b['parts'][0]['name']}")
        if material not in self.spec["materials"]:
            raise ValueError(f"unknown material {material!r}; known: {sorted(self.spec['materials'])}")
        self.model["materials"][part] = material
        return {"part": part, "material": material, "friction": self.spec["materials"][material]["friction"]}

    def set_inertial(self, mass=None, com=None, inertia=None, link=None):
        b = self.body(link or self.block)
        cur = self.model["inertial"].setdefault(b["name"], {})
        if mass is not None:
            base = self._inertial(self.model, b)
            k = mass / base["mass"]
            cur["mass"], cur["fullinertia"] = float(mass), [v * k for v in base["fullinertia"]]
        if com is not None:
            cur["com"] = [float(c) for c in com]
        if inertia is not None:
            cur["fullinertia"] = [float(inertia[0]), float(inertia[1]), float(inertia[2]), 0.0, 0.0, 0.0]
        return {"body": b["name"], **self._inertial(self.model, b)}

    def render(self, last_probe=None):
        """A MuJoCo render of the set at its designed poses; a plain schematic where MuJoCo can't render."""
        try:
            m = mj.compile(self.mjcf(free=False))
            d = mujoco.MjData(m)
            mujoco.mj_forward(m, d)
            with mujoco.Renderer(m, 480, 640) as r:
                cam = mujoco.MjvCamera()
                cam.lookat[:] = [0, 0.03, 0.15]
                cam.distance, cam.azimuth, cam.elevation = 0.75, 135, -22
                r.update_scene(d, cam)
                return _png(r.render())
        except Exception:
            import matplotlib
            matplotlib.use("Agg")
            import matplotlib.pyplot as plt
            fig, ax = plt.subplots(figsize=(6.4, 4.8), dpi=100)
            ax.text(0.5, 0.5, f"{self.spec['label']}: {len(self.spec['bodies'])} bodies\n(open the 3D view)", ha="center", va="center")
            ax.axis("off")
            buf = io.BytesIO()
            fig.savefig(buf, format="png")
            plt.close(fig)
            return buf.getvalue()


def sum_inertia(d, mk):
    r2 = (d * d).sum(1)
    I = np.zeros((3, 3))
    I += np.eye(3) * (mk * r2).sum()
    I -= (mk[:, None, None] * d[:, :, None] * d[:, None, :]).sum(0)
    return I


class UrdfObject:
    kind = "urdf"

    def __init__(self, name, spec):
        self.name, self.dir = name, os.path.join(ASSETS, name)
        self.spec = spec
        self.original = {"self_collision": spec.get("self_collision", True), "damping": {}, "frictionloss": {}}
        self.model = copy.deepcopy(self.original)
        self.check_specs = spec["checks"]
        self.urdf = os.path.join(self.dir, spec["urdf"])
        src = open(self.urdf).read()
        self.placeholder_inertia = len(re.findall(r'ixx="1\.0"[^/]*iyy="1\.0"[^/]*izz="1\.0"', src))

    def text(self, original=False):
        return json.dumps(self.original if original else self.model, indent=1, sort_keys=True) + "\n"

    def _lift(self):
        """Stand the object on the floor, upright: asset.json's euler turns a Y-up file Z-up."""
        if getattr(self, "_z", None) is None:
            g = gs()
            s = _scene()
            e = s.add_entity(g.morphs.URDF(file=self.urdf, fixed=True, euler=tuple(self.spec.get("euler", (0, 0, 0)))))
            s.build()
            self._z = -float(e.get_AABB().cpu().numpy().reshape(2, 3)[0][2]) + 0.002
        return self._z

    def _build(self, model=None, cam=False):
        g = gs()
        m = model or self.model
        z = self._lift()
        s = _scene(self_collision=m["self_collision"])
        e = s.add_entity(g.morphs.URDF(file=self.urdf, fixed=True, euler=tuple(self.spec.get("euler", (0, 0, 0))), pos=(0, 0, z)))
        c = s.add_camera(res=(640, 480), pos=(1.9, -2.6, 1.6), lookat=(0, 0, 0.5), fov=40, GUI=False) if cam else None
        s.build()
        if c is not None:
            bb = e.get_AABB().cpu().numpy().reshape(2, 3)
            ctr, size = (bb[0] + bb[1]) / 2, float(max(bb[1] - bb[0]))
            c.set_pose(pos=tuple(ctr + np.array([0.9, -1.6, 0.8]) * size), lookat=tuple(ctr))
        joints = []
        for j in e.joints:
            kind = {1: "hinge", 2: "slide"}.get(int(j.type))
            if kind is None:
                continue
            dof = int(j.dofs_idx_local[0])
            lim = e.get_dofs_limit([dof])
            joints.append({"name": j.name, "type": kind, "dof": dof, "lower": float(np.asarray(lim[0].cpu()).ravel()[0]),
                           "upper": float(np.asarray(lim[1].cpu()).ravel()[0])})
        for j in joints:
            if j["name"] in m["damping"]:
                e.set_dofs_damping(np.array([m["damping"][j["name"]]]), dofs_idx_local=[j["dof"]])
            if j["name"] in m["frictionloss"]:
                e.set_dofs_frictionloss(np.array([m["frictionloss"][j["name"]]]), dofs_idx_local=[j["dof"]])
        return s, e, joints, c

    def inspect(self):
        with _lock:
            s, e, joints, _ = self._build()
            links = [{"name": l.name, "mass_kg": round(float(l.get_mass()), 2), "collision_geoms": int(l.n_geoms)} for l in e.links]
        return {"object": self.name, "description": self.spec["description"], "label": self.spec["label"],
                "source": self.spec.get("credit"), "self_collision": self.model["self_collision"],
                "links": links, "joints": [{k: (round(v, 3) if isinstance(v, float) else v) for k, v in j.items() if k != "dof"} for j in joints],
                "links_with_placeholder_inertia": self.placeholder_inertia,
                "damping_overrides": self.model["damping"], "frictionloss_overrides": self.model["frictionloss"],
                "probes": ["joints"], "note": "Collision shapes are boxes from the URDF. Self-collision means links of this "
                                              "object collide with each other, not just with the world."}

    def set_self_collision(self, enabled):
        self.model["self_collision"] = bool(enabled)
        return {"self_collision": self.model["self_collision"]}

    def set_joint(self, name, damping=None, frictionloss=None):
        if damping is not None:
            self.model["damping"][name] = float(damping)
        if frictionloss is not None:
            self.model["frictionloss"][name] = float(frictionloss)
        return {"joint": name, "damping": self.model["damping"].get(name), "frictionloss": self.model["frictionloss"].get(name)}

    def probe(self, kind="joints", model=None):
        if kind != "joints":
            raise ValueError(f"{self.name} takes the joints probe")
        with _lock:
            s, e, joints, _ = self._build(model)
            names = {j["name"] for j in joints}
            for n in list((model or self.model)["damping"]) + list((model or self.model)["frictionloss"]):
                if n not in names:
                    raise ValueError(f"no joint {n!r}; joints: {sorted(names)}")
            reach = []
            for j in joints:
                target = j["upper"] if abs(j["upper"]) >= abs(j["lower"]) else j["lower"]
                dof = [j["dof"]]
                e.set_dofs_position(np.zeros(1), dofs_idx_local=dof, zero_velocity=True)
                e.set_dofs_kp(np.array([400.0]), dofs_idx_local=dof)
                e.set_dofs_kv(np.array([40.0]), dofs_idx_local=dof)
                e.control_dofs_position(np.array([target * 0.9]), dofs_idx_local=dof)
                for k in range(600):
                    s.step()
                    _rec(self, e, k, 20, joint=j["name"])
                got = float(e.get_dofs_position(dof).cpu().numpy().ravel()[0])
                reach.append({"joint": j["name"], "type": j["type"], "target": round(target * 0.9, 3), "reached": round(got, 3),
                              "fraction": round(got / (target * 0.9), 2) if target else None})
                e.control_dofs_position(np.zeros(1), dofs_idx_local=dof)
                for k in range(300):
                    s.step()
                    _rec(self, e, k, 20, joint=j["name"])
        return {"kind": "joints", "reach": reach, "x": [r["joint"] for r in reach], "y": [r["fraction"] for r in reach],
                "x_label": "joint", "y_label": "fraction of target reached",
                "note": "Each joint is driven by a PD controller to 90% of its limit for 1.2 s, then returned."}

    def checks(self, which="current"):
        model = self.original if which == "original" else self.model
        r = self.probe("joints", model)
        rows = []
        for c in self.check_specs:
            if c["type"] == "joints_reach":
                bad = [x["joint"] for x in r["reach"] if x["fraction"] is not None and x["fraction"] < c["min_fraction"]]
                over = [x["joint"] for x in r["reach"] if x["fraction"] is not None and x["fraction"] > 1.15]
                rows.append({"id": "all-joints-move", "result": not bad,
                             "description": f"every door or drawer reaches {int(c['min_fraction'] * 100)}% of its travel; stuck: {bad or 'none'}"})
                rows.append({"id": "within-limits", "result": not over,
                             "description": f"no joint overshoots its limit by more than 15%; overshooting: {over or 'none'}"})
        return rows, {"joints": r}, "joints"

    def render(self, last_probe=None):
        with _lock:
            s, e, joints, cam = self._build(cam=True)
            for j in joints:
                target = j["upper"] if abs(j["upper"]) >= abs(j["lower"]) else j["lower"]
                e.set_dofs_position(np.array([target * 0.5]), dofs_idx_local=[j["dof"]], zero_velocity=True)
            s.step()
            return _png(cam.render(rgb=True)[0])


def diff(obj) -> str:
    a, b = obj.text(original=True), obj.text()
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), fromfile=f"{obj.name}.as_delivered",
                                        tofile=f"{obj.name}.current")) or "(no changes)"

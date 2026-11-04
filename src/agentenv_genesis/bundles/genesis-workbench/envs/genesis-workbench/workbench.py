"""Physics core of the Genesis workbench: three sim-ready objects as editable parameters, the MJCF they compile to,
and probes that build a Genesis scene from that MJCF and measure how the object behaves."""

from __future__ import annotations

import copy
import difflib
import io
import math
import os
import tempfile
import threading

import numpy as np

MATERIALS = {
    "ceramic": {"density": 2400.0, "friction": 0.8, "band": (2000.0, 2600.0)},
    "sheet_steel": {"density": 7850.0, "friction": 0.6, "band": (7700.0, 8000.0)},
    "glass": {"density": 2500.0, "friction": 0.5, "band": (2400.0, 2600.0)},
    "oak": {"density": 750.0, "friction": 0.5, "band": (600.0, 900.0)},
    "abs_plastic": {"density": 1050.0, "friction": 0.5, "band": (1030.0, 1070.0)},
}

MUG_R, MUG_H, MUG_WALL, MUG_BASE = 0.040, 0.095, 0.0038, 0.006
DOOR_W, DOOR_H, DOOR_T = 0.60, 0.45, 0.03
DRAWER_W, DRAWER_D, DRAWER_H = 0.50, 0.46, 0.14


def mug_shell(material: str = "ceramic") -> dict:
    """Mass properties of the mug's real geometry (base disk, wall, handle) at a material's density."""
    rho = MATERIALS[material]["density"]
    r_in = MUG_R - MUG_WALL
    parts = [
        (math.pi * (MUG_R - 0.002) ** 2 * MUG_BASE, MUG_BASE / 2),
        (math.pi * (MUG_R ** 2 - r_in ** 2) * (MUG_H - MUG_BASE), MUG_BASE + (MUG_H - MUG_BASE) / 2),
        (1.0e-5, 0.050),
    ]
    vol = sum(v for v, _ in parts)
    com_z = sum(v * z for v, z in parts) / vol
    mass = rho * vol
    ixx = mass * (0.75 * (MUG_R ** 2 + r_in ** 2) / 2 + MUG_H ** 2 / 12)
    izz = mass * (MUG_R ** 2 + r_in ** 2) / 2
    return {"mass": round(mass, 4), "com": [0.0, 0.0, round(com_z, 4)], "inertia": [round(ixx, 7), round(ixx, 7), round(izz, 7)],
            "volume_cm3": round(vol * 1e6, 1)}


def door_props(mass: float) -> dict:
    ixx = mass * (DOOR_H ** 2 + DOOR_T ** 2) / 12
    iyy = mass * (DOOR_W ** 2 + DOOR_H ** 2) / 12
    izz = mass * (DOOR_W ** 2 + DOOR_T ** 2) / 12
    return {"mass": mass, "com": [0.0, 0.0, DOOR_H / 2], "inertia": [round(ixx, 6), round(iyy, 6), round(izz, 6)]}


def initial_model(name: str) -> dict:
    """The object as VLM-to-CAD delivered it, bug included."""
    if name == "mug_017":
        good = mug_shell()
        return {"name": name, "kind": "rigid", "probe": "tip",
                "description": "Ceramic coffee mug, 8 cm across and 9.5 cm tall, from an egocentric kitchen clip.",
                "links": {"body": {"material": "ceramic", "mass": good["mass"], "com": [0.0, 0.0, 0.003],
                                   "inertia": good["inertia"], "friction": 0.8, "volume_cm3": good["volume_cm3"]}},
                "joints": {}}
    if name == "oven_004":
        d = door_props(1.2)
        return {"name": name, "kind": "hinge", "probe": "hinge",
                "description": "Wall oven, 60 cm wide, with a bottom-hinged door: steel skin over a glass panel.",
                "links": {"oven": {"material": "sheet_steel", "fixed": True},
                          "door": {"material": "abs_plastic", "mass": d["mass"], "com": d["com"], "inertia": d["inertia"],
                                   "friction": 0.6, "volume_cm3": round(DOOR_W * DOOR_H * DOOR_T * 1e6 * 0.12, 1)}},
                "joints": {"door_hinge": {"type": "hinge", "parent": "oven", "child": "door", "axis": [1, 0, 0],
                                          "range": [0, 90], "damping": 0.5, "frictionloss": 4.0}}}
    if name == "cabinet_022":
        return {"name": name, "kind": "slider", "probe": "travel",
                "description": "Base cabinet, 56 cm wide, with one top drawer, oak veneer on particle board.",
                "links": {"carcass": {"material": "oak", "fixed": True},
                          "drawer": {"material": "oak", "mass": 2.0, "com": [0.0, 0.0, DRAWER_H / 2],
                                     "inertia": [0.04, 0.05, 0.07], "friction": 0.5}},
                "joints": {}}
    raise KeyError(name)


def mjcf(model: dict) -> str:
    """Compile the model to MJCF. Collision uses simple primitives; visual detail stays in the renders."""
    name = model["name"]
    L = model["links"]
    if name == "mug_017":
        b = L["body"]
        return (f'<mujoco model="{name}">\n  <compiler angle="degree"/>\n  <worldbody>\n'
                f'    <body name="body" pos="0 0 0">\n      <freejoint/>\n'
                f'      <inertial pos="{_v(b["com"])}" mass="{b["mass"]:.4f}" diaginertia="{_v(b["inertia"], 7)}"/>\n'
                f'      <geom name="shell" type="cylinder" size="{MUG_R} {MUG_H / 2}" pos="0 0 {MUG_H / 2}" friction="{b["friction"]} 0.005 0.0001"/>\n'
                f'    </body>\n  </worldbody>\n</mujoco>\n')
    if name == "oven_004":
        d = L["door"]
        j = model["joints"].get("door_hinge")
        joint = ""
        if j:
            joint = (f'        <joint name="door_hinge" type="{j["type"]}" axis="{_v(j["axis"], 0)}" range="{j["range"][0]} {j["range"][1]}"'
                     f' damping="{j["damping"]}" frictionloss="{j["frictionloss"]}" limited="true"/>\n')
        return (f'<mujoco model="{name}">\n  <compiler angle="degree"/>\n  <worldbody>\n'
                f'    <body name="oven" pos="0 0 0">\n'
                f'      <geom name="plinth" type="box" size="0.30 0.27 0.05" pos="0 0.02 0.05"/>\n'
                f'      <geom name="cavity" type="box" size="0.30 0.25 0.25" pos="0 0.04 0.35"/>\n'
                f'      <body name="door" pos="0 -0.235 0.10">\n{joint}'
                f'        <inertial pos="{_v(d["com"])}" mass="{d["mass"]:.3f}" diaginertia="{_v(d["inertia"], 6)}"/>\n'
                f'        <geom name="panel" type="box" size="{DOOR_W / 2} {DOOR_T / 2} {DOOR_H / 2}" pos="0 0 {DOOR_H / 2 + 0.002}"/>\n'
                f'      </body>\n    </body>\n  </worldbody>\n</mujoco>\n')
    if name == "cabinet_022":
        d = L["drawer"]
        j = model["joints"].get("drawer_slide")
        joint = ""
        if j:
            joint = (f'        <joint name="drawer_slide" type="{j["type"]}" axis="{_v(j["axis"], 0)}" range="{j["range"][0]} {j["range"][1]}"'
                     f' damping="{j["damping"]}" frictionloss="{j["frictionloss"]}" limited="true"/>\n')
        return (f'<mujoco model="{name}">\n  <compiler angle="degree"/>\n  <worldbody>\n'
                f'    <body name="carcass" pos="0 0 0">\n'
                f'      <geom name="bottom" type="box" size="0.28 0.26 0.01" pos="0 0 0.60"/>\n'
                f'      <geom name="top" type="box" size="0.28 0.26 0.01" pos="0 0 0.80"/>\n'
                f'      <geom name="left" type="box" size="0.01 0.26 0.10" pos="-0.27 0 0.70"/>\n'
                f'      <geom name="right" type="box" size="0.01 0.26 0.10" pos="0.27 0 0.70"/>\n'
                f'      <geom name="back" type="box" size="0.28 0.01 0.10" pos="0 0.25 0.70"/>\n'
                f'      <geom name="base" type="box" size="0.28 0.26 0.295" pos="0 0 0.295"/>\n'
                f'      <body name="drawer" pos="0 0.0 0.615">\n{joint}'
                f'        <inertial pos="{_v(d["com"])}" mass="{d["mass"]:.3f}" diaginertia="{_v(d["inertia"], 5)}"/>\n'
                f'        <geom name="tray" type="box" size="{DRAWER_W / 2} {DRAWER_D / 2} 0.006" pos="0 -0.005 0.008"/>\n'
                f'        <geom name="front" type="box" size="0.275 0.01 {DRAWER_H / 2 + 0.02:.3f}" pos="0 -0.245 {DRAWER_H / 2}"/>\n'
                f'        <geom name="handle" type="box" size="0.08 0.012 0.008" pos="0 -0.27 {DRAWER_H / 2 + 0.02}"/>\n'
                f'      </body>\n    </body>\n  </worldbody>\n</mujoco>\n')
    raise KeyError(name)


def _v(xs, nd=4) -> str:
    return " ".join(f"{float(x):.6g}" for x in xs)


def diff(a: dict, b: dict) -> str:
    return "".join(difflib.unified_diff(mjcf(a).splitlines(True), mjcf(b).splitlines(True),
                                        fromfile=f"{a['name']}.as_delivered.xml", tofile=f"{b['name']}.current.xml"))


class Sim:
    """Owns Genesis. gs.init runs once in a dedicated thread; every probe builds a fresh scene from the model's MJCF,
    so compiled kernels are reused and only the first build pays for compilation."""

    def __init__(self):
        self._lock = threading.Lock()
        self._gs = None
        self._dir = tempfile.mkdtemp(prefix="genesis-workbench-")

    def _init(self):
        if self._gs is None:
            import genesis as gs
            if not getattr(gs, "_initialized", False):
                gs.init(backend=gs.cpu if os.environ.get("GENESIS_BACKEND", "cpu") == "cpu" else gs.gpu, seed=0,
                        logging_level="warning")
            self._gs = gs
        return self._gs

    def _scene(self, model, n_envs, friction_floor=2.0, dt=0.002):
        gs = self._init()
        path = os.path.join(self._dir, f"{model['name']}.xml")
        with open(path, "w") as f:
            f.write(mjcf(model))
        scene = gs.Scene(sim_options=gs.options.SimOptions(dt=dt), show_viewer=False)
        scene.add_entity(gs.morphs.Plane(), material=gs.materials.Rigid(friction=friction_floor))
        ent = scene.add_entity(gs.morphs.MJCF(file=path))
        scene.build(n_envs=n_envs)
        return scene, ent

    def probe(self, model: dict, kind: str | None = None) -> dict:
        kind = kind or model["probe"]
        with self._lock:
            if kind == "tip":
                return self._tip(model)
            if kind == "rest":
                return self._rest(model)
            if kind == "hinge":
                return self._hinge(model)
            if kind == "travel":
                return self._travel(model)
        raise ValueError(f"unknown probe {kind!r}; use tip, rest, hinge or travel")

    def _tip(self, model):
        import torch
        leans = [20, 26, 32, 36, 40, 43, 46, 49, 52, 56, 62, 70]
        scene, ent = self._scene(model, len(leans))
        ramp, hold = 300, 400
        for k in range(ramp + hold):
            f = min(1.0, k / ramp)
            g = [[9.81 * math.sin(math.radians(a) * f), 0.0, -9.81 * math.cos(math.radians(a) * f)] for a in leans]
            scene.rigid_solver.set_gravity(torch.tensor(g, dtype=torch.float32))
            scene.step()
        w, x, y, z = ent.get_quat().cpu().numpy().T
        tilt = np.degrees(np.arccos(np.clip(1 - 2 * (x * x + y * y), -1, 1)))
        fell = [bool(t > 30) for t in tilt]
        first = next((a for a, f in zip(leans, fell) if f), None)
        return {"kind": "tip", "x": leans, "x_label": "tilt-table angle (deg)", "y_label": "final tilt of the mug (deg)",
                "y": [round(float(t), 1) for t in tilt], "toppled": fell, "tips_at_deg": first,
                "note": "Gravity is rotated per environment to tilt the table, ramped over 0.6 s and held 0.8 s."}

    def _rest(self, model):
        scene, ent = self._scene(model, 1)
        for _ in range(500):
            scene.step()
        v = float(np.abs(ent.get_dofs_velocity().cpu().numpy()).max())
        w, x, y, z = ent.get_quat().cpu().numpy()[0]
        tilt = math.degrees(math.acos(max(-1, min(1, 1 - 2 * (x * x + y * y)))))
        return {"kind": "rest", "max_speed": round(v, 4), "tilt_deg": round(tilt, 2), "stable": v < 0.01 and tilt < 2}

    def _hinge(self, model):
        scene, ent = self._scene(model, 1)
        if "door_hinge" not in model["joints"]:
            return {"kind": "hinge", "error": "no door_hinge joint; the door is welded shut"}
        dof = [ent.get_joint("door_hinge").dofs_idx_local[0]]
        ent.set_dofs_position(np.array([[math.radians(5)]]), dofs_idx_local=dof, zero_velocity=True)
        ts, angles = [], []
        for k in range(1001):
            if k % 50 == 0:
                ts.append(round(k * 0.002, 2))
                angles.append(round(math.degrees(float(ent.get_dofs_position(dof).cpu().numpy().ravel()[0])), 1))
            scene.step()
        reach = next((t for t, a in zip(ts, angles) if a >= 85), None)
        return {"kind": "hinge", "x": ts, "x_label": "time after unlatching (s)", "y_label": "door angle (deg)", "y": angles,
                "final_deg": angles[-1], "opens_at_s": reach,
                "note": "Door released at 5 deg open with zero velocity, 2.0 s simulated."}

    def _travel(self, model):
        scene, ent = self._scene(model, 1)
        link = ent.get_link("drawer")
        y0 = float(link.get_pos().cpu().numpy().ravel()[1])
        ts, travel = [], []
        for k in range(751):
            if k < 300:
                link.apply_external_force(np.array([[0.0, -10.0, 0.0]]))
            if k % 25 == 0:
                ts.append(round(k * 0.002, 2))
                travel.append(round(y0 - float(link.get_pos().cpu().numpy().ravel()[1]), 4))
            scene.step()
        z = float(link.get_pos().cpu().numpy().ravel()[2])
        return {"kind": "travel", "x": ts, "x_label": "time (s); 10 N pull for the first 0.6 s", "y_label": "drawer travel (m)",
                "y": travel, "final_m": travel[-1], "max_m": max(travel), "drawer_z": round(z, 3),
                "note": "A 10 N pull on the drawer front toward the user for 0.6 s, then 0.9 s coasting."}


def suite(sim: Sim, model: dict) -> list[dict]:
    """The hidden acceptance checks for the loaded object."""
    name, rows = model["name"], []
    if name == "mug_017":
        ref = mug_shell()
        ref_angle = math.degrees(math.atan(MUG_R / ref["com"][2]))
        tip = sim.probe(model, "tip")
        rest = sim.probe(model, "rest")
        b = model["links"]["body"]
        got = tip["tips_at_deg"]
        rows.append({"id": "tips-like-a-real-mug", "result": got is not None and abs(got - ref_angle) <= 6,
                     "description": f"tips on a tilt table within 6 deg of a real mug ({ref_angle:.1f} deg); measured {got}"})
        rows.append({"id": "ceramic-mass", "result": 0.20 <= b["mass"] <= 0.40,
                     "description": f"mass within the ceramic band 0.20-0.40 kg; got {b['mass']:.3f} kg"})
        rows.append({"id": "rests-upright", "result": rest["stable"],
                     "description": f"rests upright on a flat bench; tilt {rest['tilt_deg']} deg, speed {rest['max_speed']}"})
        return rows, {"tip": tip, "rest": rest}
    if name == "oven_004":
        h = sim.probe(model, "hinge")
        d = model["links"]["door"]
        opens = h.get("opens_at_s")
        rows.append({"id": "swings-open", "result": opens is not None and opens <= 2.0,
                     "description": f"released at 5 deg, swings open to at least 85 deg within 2 s; reached at {opens}"})
        rows.append({"id": "stops-at-limit", "result": "y" in h and max(h["y"]) <= 92,
                     "description": f"stops at its 90 deg limit; max {max(h['y']) if 'y' in h else None}"})
        rows.append({"id": "door-mass", "result": 3.0 <= d["mass"] <= 10.0,
                     "description": f"mass within a steel-and-glass oven door's band 3-10 kg; got {d['mass']:.2f} kg"})
        return rows, {"hinge": h}
    if name == "cabinet_022":
        t = sim.probe(model, "travel")
        j = model["joints"].get("drawer_slide")
        rows.append({"id": "slides-open", "result": t["max_m"] >= 0.30,
                     "description": f"a 10 N pull opens it at least 0.30 m; max travel {t['max_m']} m"})
        rows.append({"id": "stops-before-falling-out", "result": t["max_m"] <= 0.47 and t["drawer_z"] > 0.55,
                     "description": f"stops before it leaves the cabinet (at most 0.47 m, still supported); max {t['max_m']} m"})
        rows.append({"id": "slides-along-depth", "result": bool(j) and j.get("type") == "slide"
                     and abs(abs(float(j["axis"][1])) - 1) < 1e-6,
                     "description": "has a slide joint along the cabinet's depth axis"})
        return rows, {"travel": t}
    raise KeyError(name)


def render(model: dict, probe: dict | None = None, view: str = "side") -> bytes:
    """A schematic engineering render of the current model: geometry, centre of mass, joints, and the last probe."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Circle, FancyArrowPatch, Polygon, Rectangle

    ink, acc, mute, fail, ok = "#14191B", "#0B7A72", "#7A8589", "#BF3B2E", "#2B8A4C"
    fig = plt.figure(figsize=(8.0, 4.2), dpi=110)
    ax = fig.add_axes([0.04, 0.12, 0.5, 0.8]) if probe and "x" in probe else fig.add_axes([0.06, 0.1, 0.88, 0.82])
    ax.set_aspect("equal")
    ax.axis("off")
    name = model["name"]
    if name == "mug_017":
        b = model["links"]["body"]
        ax.add_patch(Rectangle((-4, 0), 8, 9.5, fill=False, lw=2, ec=ink))
        ax.add_patch(Rectangle((-3.62, 0.6), 7.24, 8.9, fill=True, fc="#E8ECE9", ec=mute, lw=1))
        ax.add_patch(Polygon([[-4, 2.5], [-6.3, 3.0], [-6.3, 7.0], [-4, 7.5]], closed=True, fill=False, ec=ink, lw=2))
        cz = b["com"][2] * 100
        ax.add_patch(Circle((0, cz), 0.45, color=acc, zorder=5))
        ax.plot([0, 0], [cz, 0], ls="--", color=acc, lw=1.2)
        ang = math.degrees(math.atan(4 / max(cz, 1e-3)))
        ax.plot([4, 4 - 7 * math.sin(math.radians(ang))], [0, 7 * math.cos(math.radians(ang))], color=acc, lw=1)
        ax.text(0.7, cz, f"COM {cz:.1f} cm", color=acc, fontsize=10, va="center")
        ax.text(4.3, 0.4, f"tips past {ang:.1f}° (statics)", color=acc, fontsize=9)
        ax.plot([-8, 9], [0, 0], color=mute, lw=1)
        ax.set_xlim(-8, 11)
        ax.set_ylim(-1.2, 11)
        title = f"mug_017 · {b['mass']:.3f} kg · COM z {cz:.1f} cm"
    elif name == "oven_004":
        d = model["links"]["door"]
        j = model["joints"].get("door_hinge")
        a = math.radians((probe or {}).get("final_deg", 0) if probe and probe.get("kind") == "hinge" else 0)
        ax.add_patch(Rectangle((0, 0), 52, 10, fill=False, ec=mute, lw=1.5))
        ax.add_patch(Rectangle((2, 10), 50, 50, fill=False, ec=ink, lw=2))
        hx, hy = 0, 10
        L = 45
        ax.plot([hx, hx - L * math.sin(a)], [hy, hy + L * math.cos(a)], color=ink, lw=6, solid_capstyle="butt")
        ax.add_patch(Circle((hx, hy), 1.2, color=acc if j else fail, zorder=5))
        cx, cy = hx - 22.5 * math.sin(a), hy + 22.5 * math.cos(a)
        ax.add_patch(Circle((cx, cy), 1.0, color=acc, zorder=6))
        ax.text(hx + 2, hy - 4, ("hinge: frictionloss %.2f N·m, damping %.2f" % (j["frictionloss"], j["damping"])) if j else "no hinge joint",
                fontsize=9, color=acc if j else fail)
        ax.set_xlim(-50, 60)
        ax.set_ylim(-8, 62)
        title = f"oven_004 door · {d['mass']:.2f} kg · {('open %.0f°' % math.degrees(a)) if a else 'closed'}"
    else:
        dr = model["links"]["drawer"]
        j = model["joints"].get("drawer_slide")
        t = (probe or {}).get("final_m", 0) if probe and probe.get("kind") == "travel" else 0
        ax.add_patch(Rectangle((-26, 0), 52, 82, fill=False, ec=ink, lw=2))
        ax.add_patch(Rectangle((-26, 60), 52, 20, fill=False, ec=mute, lw=1))
        ox = -26 - t * 100
        ax.add_patch(Rectangle((ox, 61.5), 46, 14, fill=True, fc="#E8ECE9", ec=ink, lw=1.5))
        ax.add_patch(Rectangle((ox - 2, 61), 2, 18, fill=True, fc=ink))
        if j:
            ax.add_patch(FancyArrowPatch((ox + 10, 68), (ox - 14, 68), arrowstyle="->", mutation_scale=14, color=acc))
        ax.text(-24, 86, (f"slide joint · range {j['range'][0]}-{j['range'][1]} m · damping {j['damping']}") if j else "no joint: drawer welded to carcass",
                fontsize=9, color=acc if j else fail)
        ax.set_xlim(-80, 30)
        ax.set_ylim(-4, 92)
        title = f"cabinet_022 drawer (side view) · {dr['mass']:.2f} kg · out {t:.2f} m"
    ax.set_title(title, fontsize=11, color=ink, loc="left")
    if probe and "x" in probe:
        px = fig.add_axes([0.62, 0.16, 0.35, 0.72])
        px.plot(probe["x"], probe["y"], marker="o", ms=3.5, color=acc, lw=1.8)
        px.set_xlabel(probe["x_label"], fontsize=8.5, color=ink)
        px.set_ylabel(probe["y_label"], fontsize=8.5, color=ink)
        px.tick_params(labelsize=8)
        px.grid(alpha=0.3)
        px.set_title(f"{probe['kind']} probe", fontsize=10, color=ink, loc="left")
        for s in ("top", "right"):
            px.spines[s].set_visible(False)
    buf = io.BytesIO()
    fig.savefig(buf, format="png", facecolor="white")
    plt.close(fig)
    return buf.getvalue()


def apply_material(model: dict, link: str, material: str) -> dict:
    m = MATERIALS[material]
    L = model["links"][link]
    L["material"] = material
    L["friction"] = m["friction"]
    if model["name"] == "mug_017":
        L.update({k: v for k, v in mug_shell(material).items() if k in ("mass", "com", "inertia", "volume_cm3")})
    elif model["name"] == "oven_004" and link == "door":
        skin = {"sheet_steel": 0.0012, "abs_plastic": 0.0015, "oak": 0.008, "glass": 0.005, "ceramic": 0.004}.get(material, 0.0015)
        glass_window = 1.9 if material == "sheet_steel" else 0.0
        L.update(door_props(round(m["density"] * 2 * DOOR_W * DOOR_H * skin + glass_window, 3)))
    return L


def clone(model: dict) -> dict:
    return copy.deepcopy(model)

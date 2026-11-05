"""MuJoCo, the engine the workbench grades in: the objects ship as MJCF and OBJ for MuJoCo, so they pass or fail where
they will run. Genesis gives a second opinion (assets.py).

Every probe builds a MuJoCo model from the object's MJCF on a floor with friction 1.0, steps it in 2 ms frames of two
1 ms steps, and can record each body's pose per frame ([x, y, z, qw, qx, qy, qz], z up) for the browser to replay.

Contacts are stiffer than MuJoCo's default: a time constant of 3 ms against 20 ms, which needs the 1 ms step. A soft
contact lets a 2 mm knife blade sink further into a wall than the blade is thick when a set falls over, and the wall
then pushes it out the far side. Every colliding geom the workbench writes carries it, so an exported MJCF behaves the
same in another scene stepped at 1 ms."""

from __future__ import annotations

import math
import re

import mujoco
import numpy as np
from scipy.spatial import ConvexHull

DT = 0.002  # a frame: what probes count in and traces record
SUBSTEPS = 2  # MuJoCo steps per frame
LEANS = list(range(10, 75, 5))
SOLREF = "0.003 1"
FLOOR = f'<geom name="floor" type="plane" size="8 8 0.1" friction="1 0.005 0.0001" solref="{SOLREF}"/>'


def compile(doc: str, extra: str = "") -> mujoco.MjModel:
    """The object's MJCF on the workbench floor, with `extra` bodies added to the world."""
    doc = doc.replace("<worldbody>", f"<worldbody>\n    {FLOOR}{extra}", 1)
    head = re.match(r'\s*<mujoco model="[^"]*">', doc).group(0)
    doc = doc.replace(head, head + f'\n  <option timestep="{DT / SUBSTEPS:g}"/>', 1)
    return mujoco.MjModel.from_xml_string(doc)


def step(m, d):
    """One frame."""
    for _ in range(SUBSTEPS):
        mujoco.mj_step(m, d)


def unstable(d) -> bool:
    """MuJoCo found a bad acceleration and reset the state at some point, so the run means nothing."""
    return bool(d.warning[mujoco.mjtWarning.mjWARN_BADQACC].number)


def place(doc: str, body: str, pos) -> str:
    return doc.replace(f'<body name="{body}">', f'<body name="{body}" pos="{pos[0]:.5f} {pos[1]:.5f} {pos[2]:.5f}">', 1)


def tilt(q) -> float:
    w, x, y, z = q
    return math.degrees(math.acos(max(-1.0, min(1.0, 1 - 2 * (x * x + y * y)))))


def rotation_deg(q, q0) -> float:
    """The angle between two orientations."""
    d = abs(float(np.dot(q, q0))) / (np.linalg.norm(q) * np.linalg.norm(q0))
    return math.degrees(2 * math.acos(min(1.0, d)))


def lowest(m, d, body=None) -> float:
    """The lowest point of the colliding mesh geoms (of `body`, when given)."""
    zs = []
    for g in range(m.ngeom):
        if m.geom_contype[g] == 0 or m.geom_type[g] != mujoco.mjtGeom.mjGEOM_MESH:
            continue
        if body is not None and m.geom_bodyid[g] != body:
            continue
        k = m.geom_dataid[g]
        v = m.mesh_vert[m.mesh_vertadr[k]:m.mesh_vertadr[k] + m.mesh_vertnum[k]]
        zs.append(float((v @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g])[:, 2].min()))
    return min(zs) if zs else 0.0


class Recorder:
    """Each named body's pose every `every` steps, and anything passed as extra."""

    def __init__(self, m, every: int, bodies: list[str] | None = None):
        names = [m.body(i).name for i in range(1, m.nbody)]
        self.names = bodies or [n for n in names if not n.startswith("ball")]
        self.ids = [m.body(n).id for n in self.names]
        self.every, self.frames, self.t, self.extra = every, [], [], {}

    def __call__(self, d, k, **extra):
        if k % self.every:
            return
        self.frames.append(np.round(np.concatenate([d.xpos[self.ids], d.xquat[self.ids]], 1), 5).tolist())
        self.t.append(round(len(self.t) * self.every * DT, 3))
        for key, val in extra.items():
            self.extra.setdefault(key, []).append(val)

    def trace(self, **more) -> dict:
        return {"engine": "mujoco", "bodies": self.names, "t": self.t, "frames": self.frames, **self.extra, **more}


def _mesh(m, g):
    k = m.geom_dataid[g]
    v = m.mesh_vert[m.mesh_vertadr[k]:m.mesh_vertadr[k] + m.mesh_vertnum[k]].astype(float)
    return v, m.mesh_face[m.mesh_faceadr[k]:m.mesh_faceadr[k] + m.mesh_facenum[k]]


def _surface(v, f, spacing, rng):
    """A mesh's vertices and points spread over its faces by area, about `spacing` apart."""
    tri = v[f]
    area = 0.5 * np.linalg.norm(np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0]), axis=1)
    if area.sum() <= 0:
        return v
    n = int(np.clip(area.sum() / spacing ** 2, 200, 20000))
    i = rng.choice(len(f), n, p=area / area.sum())
    s, r = np.sqrt(rng.random(n))[:, None], rng.random(n)[:, None]
    return np.concatenate([v, (1 - s) * tri[i, 0] + s * (1 - r) * tri[i, 1] + s * r * tri[i, 2]])


def _to_body(m, g, pts):
    R = np.zeros(9)
    mujoco.mju_quat2Mat(R, m.geom_quat[g])
    return pts @ R.reshape(3, 3).T + m.geom_pos[g]


class Overlap:
    """How deep any body's surface goes into another body's solid, or below the floor, over the frames it sees. MuJoCo
    collides each mesh geom as its convex hull, so a body's solid is its colliding hulls, and a point's depth is how far
    inside the hull that holds it deepest. A soft contact lets bodies sink in a little; a thin part that sinks further
    than it is thick can be pushed out the far side of a wall, which contact depths alone don't show."""

    def __init__(self, m, spacing=0.004, seed=0):
        rng = np.random.default_rng(seed)
        self.max, self.last, vis, col = {}, {}, {}, {}
        for g in range(m.ngeom):
            b = int(m.geom_bodyid[g])
            if b == 0 or m.geom_type[g] != mujoco.mjtGeom.mjGEOM_MESH:
                continue
            v, f = _mesh(m, g)
            (col if m.geom_contype[g] or m.geom_conaffinity[g] else vis).setdefault(b, []).append((g, v, f))
        self.points, self.hulls = {}, {}
        for b in set(vis) | set(col):
            self.points[b] = np.concatenate([_to_body(m, g, _surface(v, f, spacing, rng)) for g, v, f in vis.get(b) or col[b]])
        for b, geoms in col.items():
            hulls = []
            for g, v, _ in geoms:
                vb = _to_body(m, g, v)
                eq = ConvexHull(vb).equations
                hulls.append((eq[:, :3], eq[:, 3], vb.min(0), vb.max(0)))
            lo, hi = np.min([h[2] for h in hulls], 0), np.max([h[3] for h in hulls], 0)
            corners = np.array([[x, y, z] for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])])
            self.hulls[b] = (hulls, lo, hi, corners)
        self.names = {b: m.body(b).name for b in self.points}

    def __call__(self, d, **where):
        world = {b: P @ d.xmat[b].reshape(3, 3).T + d.xpos[b] for b, P in self.points.items()}
        self.last = {}
        for a, P in world.items():
            self._keep((a, 0), float(-P[:, 2].min()), where)
            for b, (hulls, lo, hi, corners) in self.hulls.items():
                if b == a:
                    continue
                R, x = d.xmat[b].reshape(3, 3), d.xpos[b]
                c = corners @ R.T + x  # b's bounds in the world, to drop most of a's points before transforming them
                near = P[np.all((P >= c.min(0)) & (P <= c.max(0)), 1)]
                deep = 0.0
                if len(near):
                    L = (near - x) @ R
                    L = L[np.all((L >= lo) & (L <= hi), 1)]
                    for A, off, hlo, hhi in hulls if len(L) else ():
                        Q = L[np.all((L >= hlo) & (L <= hhi), 1)]
                        if len(Q):
                            deep = max(deep, float((-(Q @ A.T + off).max(1)).max()))
                self._keep((a, b), deep, where)

    def _keep(self, key, depth, where):
        self.last[key] = (depth, dict(where))
        if depth > self.max.get(key, (0.0,))[0]:
            self.max[key] = (depth, dict(where))

    def _rows(self, kept):
        return [{"body": self.names[a], "into": self.names.get(b, "floor"), "mm": round(v * 1000, 2), **w}
                for (a, b), (v, w) in kept.items() if v > 5e-5]

    def summary(self) -> dict:
        """Each overlapping pair at its deepest, deepest first: `body`'s surface `mm` inside `into` (a body, or "floor"),
        with when and where in the run that was; and the same at the run's last frame, once everything has settled."""
        return {**worst(self._rows(self.max)), "end": worst(self._rows(self.last))}


def worst(rows) -> dict:
    """Overlap rows (from one run or several) as a summary: each pair once at its deepest, the deepest of all, and the
    deepest into another body and into the floor."""
    keep = {}
    for r in rows:
        k = (r["body"], r["into"])
        if r["mm"] > keep.get(k, {"mm": -1})["mm"]:
            keep[k] = r
    rows = sorted(keep.values(), key=lambda r: -r["mm"])
    bodies = [r for r in rows if r["into"] != "floor"]
    floor = [r for r in rows if r["into"] == "floor"]
    return {"max_mm": rows[0]["mm"] if rows else 0.0, "pairs": rows,
            "bodies": bodies[0] if bodies else None, "floor": floor[0] if floor else None}


def deepest(summaries) -> dict:
    summaries = list(summaries)
    return {**worst([r for s in summaries for r in s["pairs"]]), "end": worst([r for s in summaries for r in s["end"]["pairs"]])}


# Probes on one free body (mesh objects); the same measures and thresholds as the Genesis probes.

def rest(doc: str, lift: float, trace=False):
    m = compile(place(doc, "body", (0, 0, lift)))
    d = mujoco.MjData(m)
    rec = Recorder(m, 10) if trace else None
    for k in range(600):
        step(m, d)
        rec and rec(d, k)
    v, t = float(np.abs(d.qvel).max()), tilt(d.qpos[3:7])
    out = {"kind": "rest", "engine": "mujoco", "max_speed": round(v, 4), "tilt_deg": round(t, 2), "stable": bool(v < 0.02 and t < 3)}
    return out, rec and rec.trace(kind="rest")


def drop(doc: str, lift: float, height: float, trace=False):
    m = compile(place(doc, "body", (0, 0, lift + 2 * height)))
    d = mujoco.MjData(m)
    rec = Recorder(m, 8) if trace else None
    settle, pen, ts, zs = None, 0.0, [], []
    for k in range(1200):
        step(m, d)
        rec and rec(d, k)
        if k % 25 == 0:
            z0 = lowest(m, d)
            pen = max(pen, -z0)
            ts.append(round(k * DT, 2))
            zs.append(round(z0 * 100, 2))
            if settle is None and k > 50 and float(np.abs(d.qvel).max()) < 0.02:
                settle = round(k * DT, 2)
    out = {"kind": "drop", "engine": "mujoco", "x": ts, "x_label": "time (s)", "y_label": "lowest point (cm)", "y": zs,
           "settles_s": settle, "max_penetration_mm": round(pen * 1000, 1), "final_tilt_deg": round(tilt(d.qpos[3:7]), 1)}
    return out, rec and rec.trace(kind="drop")


def tip(doc: str, lift: float, trace=False):
    m = compile(place(doc, "body", (0, 0, lift)))
    d = mujoco.MjData(m)
    tilts, slides, recs = [], [], []
    for a in LEANS:
        mujoco.mj_resetData(m, d)
        mujoco.mj_forward(m, d)
        p0 = d.qpos[:2].copy()
        rec = Recorder(m, 10) if trace else None
        for k in range(900):
            f = min(1.0, k / 400)
            m.opt.gravity[:] = [9.81 * math.sin(math.radians(a) * f), 0.0, -9.81 * math.cos(math.radians(a) * f)]
            step(m, d)
            rec and rec(d, k, ramp=round(f, 4))
        tilts.append(tilt(d.qpos[3:7]))
        slides.append(float(np.linalg.norm(d.qpos[:2] - p0)))
        recs.append(rec)
    mode = ["topple" if t > 30 else "slide" if s > 0.03 else "holds" for t, s in zip(tilts, slides)]
    i = next((k for k, md in enumerate(mode) if md != "holds"), len(LEANS) - 1)
    first = (LEANS[i], mode[i]) if mode[i] != "holds" else (None, "holds")
    out = {"kind": "tip", "engine": "mujoco", "x": LEANS, "x_label": "tilt-table angle (deg)", "y_label": "final tilt (deg)",
           "y": [round(t, 1) for t in tilts], "modes": mode, "first_failure_deg": first[0], "mode": first[1],
           "note": "Friction 1.0 on the table. 'slide' means it slid more than 3 cm before tipping."}
    return out, recs[i] and recs[i].trace(kind="tip", lean_deg=LEANS[i])


def cavity(doc_fixed: str, lift: float, bounds, trace=False):
    top = float(bounds[1][2] - bounds[0][2])
    cx, cy = [float(v) for v in (np.asarray(bounds[0][:2]) + np.asarray(bounds[1][:2])) / 2]
    balls = "".join(f'\n    <body name="ball{i}" pos="{cx + 0.004 * (i - 2):.4f} {cy + 0.003 * (i % 2):.4f} {lift + top + 0.03 + 0.025 * i:.4f}">'
                    f'<freejoint/><geom type="sphere" size="0.008" density="1000" solref="{SOLREF}"/></body>' for i in range(5))
    m = compile(place(doc_fixed, "body", (0, 0, lift)), balls)
    d = mujoco.MjData(m)
    rec = Recorder(m, 10, ["body"]) if trace else None
    ids = [m.body(f"ball{i}").id for i in range(5)]
    for k in range(1000):
        step(m, d)
        rec and rec(d, k, balls=np.round(d.xpos[ids], 4).tolist())
    zs = [float(d.xpos[i][2]) for i in ids]
    inside = sum(1 for z in zs if z < lift + top - 0.006)
    out = {"kind": "cavity", "engine": "mujoco", "rim_height_cm": round(top * 100, 1), "ball_heights_cm": [round(z * 100, 1) for z in zs],
           "balls_inside": inside, "open": bool(inside >= 3), "note": "Five 8 mm spheres dropped into the opening of the object held in place."}
    return out, rec and rec.trace(kind="cavity")


# Probes on a set: a block and the knives seated in it, every body free.

def _pose(d, i):
    return np.concatenate([d.xpos[i], d.xquat[i]])


def _relative(block, body):
    """`body`'s pose in `block`'s frame, both as [x, y, z, qw, qx, qy, qz]."""
    qb_inv = np.array([block[3], -block[4], -block[5], -block[6]])
    p = np.zeros(3)
    mujoco.mju_rotVecQuat(p, body[:3] - block[:3], qb_inv)
    q = np.zeros(4)
    mujoco.mju_mulQuat(q, qb_inv, body[3:])
    return p, q


def _seat_error(d, block_id, knife_id, seat, axis=None):
    """How far a knife is from its seat in the block (m) and turned from it (deg); with `axis`, also how far it sticks
    out of its slot along it (m, positive when out)."""
    p, q = _relative(_pose(d, block_id), _pose(d, knife_id))
    dp = p - np.asarray(seat[:3])
    out = (float(np.linalg.norm(dp)), rotation_deg(q, np.asarray(seat[3:])))
    return out + (float(dp @ np.asarray(axis)),) if axis is not None else out


def set_rest(doc, block: str, seats: dict, axis, trace=False, steps=750):
    """Every body starts at its designed pose; after `steps`, how far each knife has moved from its seat in the block."""
    m = compile(doc)
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    rec = Recorder(m, 10) if trace else None
    ov = Overlap(m)
    ov(d, t=0.0)
    b = m.body(block).id
    p0 = d.xpos[b].copy()
    for k in range(steps):
        step(m, d)
        rec and rec(d, k)
        k % 10 or ov(d, t=round(k * DT, 3))
    knives = {}
    for name, seat in seats.items():
        shift, rot, out_ = _seat_error(d, b, m.body(name).id, seat, axis)
        knives[name] = {"shift_mm": round(shift * 1000, 2), "out_of_slot_mm": round(out_ * 1000, 2), "rotation_deg": round(rot, 2)}
    out = {"kind": "rest", "engine": "mujoco", "knives": knives, "block_tilt_deg": round(tilt(d.xquat[b]), 2),
           "block_slide_mm": round(float(np.linalg.norm(d.xpos[b][:2] - p0[:2])) * 1000, 2),
           "max_speed": round(float(np.abs(d.qvel).max()), 4), "overlap": ov.summary(), "unstable": unstable(d)}
    return out, rec and rec.trace(kind="rest")


def set_seat(doc, block: str, seats: dict, axis, lift=0.02, trace=False, steps=1000):
    """Each knife starts `lift` above its seat along its slot and drops in; how far from its seat it ends."""
    m = compile(doc)
    d = mujoco.MjData(m)
    for name in seats:
        j = m.body(name).jntadr[0]
        d.qpos[m.jnt_qposadr[j]:m.jnt_qposadr[j] + 3] += np.asarray(axis) * lift
    mujoco.mj_forward(m, d)
    rec = Recorder(m, 10) if trace else None
    ov = Overlap(m)
    ov(d, t=0.0)
    b = m.body(block).id
    for k in range(steps):
        step(m, d)
        rec and rec(d, k)
        k % 10 or ov(d, t=round(k * DT, 3))
    knives = {}
    for name, seat in seats.items():
        shift, rot, out_ = _seat_error(d, b, m.body(name).id, seat, axis)
        knives[name] = {"shift_mm": round(shift * 1000, 1), "out_of_slot_mm": round(out_ * 1000, 1), "rotation_deg": round(rot, 1)}
    out = {"kind": "seat", "engine": "mujoco", "lift_mm": lift * 1000, "knives": knives, "overlap": ov.summary(), "unstable": unstable(d)}
    return out, rec and rec.trace(kind="seat")


def set_extract(doc, block: str, seats: dict, axis, trace=False, ratio_max=3.0):
    """Pull each knife out along its slot in turn, the force rising to `ratio_max` times its weight over 1 s: the force
    at which it starts to come out (2 mm), and how far the block lifts or tips meanwhile."""
    m = compile(doc)
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    rec = Recorder(m, 10) if trace else None
    ov = Overlap(m)
    ov(d, t=0.0)
    b = m.body(block).id
    z0 = float(d.xpos[b][2])
    knives, k, window = {}, 0, 600
    for name in seats:
        i = m.body(name).id
        weight = float(m.body_subtreemass[i]) * 9.81
        free_at, lift, tilt_max = None, 0.0, 0.0
        start = _seat_error(d, b, i, seats[name], axis)[2]
        for s in range(window):
            frac = min(1.0, s / 500)
            ax = np.zeros(3)
            mujoco.mju_rotVecQuat(ax, np.asarray(axis, dtype=float), d.xquat[b])
            d.xfrc_applied[i, :3] = ax * weight * ratio_max * frac if free_at is None else 0.0
            step(m, d)
            rec and rec(d, k, pulling=name if free_at is None else None)
            k % 10 or ov(d, t=round(k * DT, 3), pulling=name)
            k += 1
            lift = max(lift, float(d.xpos[b][2]) - z0)
            tilt_max = max(tilt_max, tilt(d.xquat[b]))
            if free_at is None and _seat_error(d, b, i, seats[name], axis)[2] - start > 0.002:
                free_at = round(ratio_max * frac, 2)
        d.xfrc_applied[i, :] = 0.0
        for s in range(150):  # let it fall back in and the set settle before the next knife
            step(m, d)
            rec and rec(d, k, pulling=None)
            k % 10 or ov(d, t=round(k * DT, 3), pulling=name)
            k += 1
        knives[name] = {"freed_at_weight_ratio": free_at, "block_lift_mm": round(lift * 1000, 2), "block_tilt_deg": round(tilt_max, 2)}
    return ({"kind": "extract", "engine": "mujoco", "ratio_max": ratio_max, "knives": knives, "overlap": ov.summary(),
             "unstable": unstable(d)}, rec and rec.trace(kind="extract"))


def set_tip(doc, block: str, seats: dict, trace=False, leans=LEANS):
    """The set on a tilting table: the first angle where the block tips or slides, or a knife slides 10 mm out of its slot."""
    m = compile(doc)
    d = mujoco.MjData(m)
    b = m.body(block).id
    rows, recs = [], []
    for a in leans:
        mujoco.mj_resetData(m, d)
        mujoco.mj_forward(m, d)
        p0 = d.xpos[b][:2].copy()
        rec = Recorder(m, 10) if trace else None
        ov = Overlap(m)
        ov(d, t=0.0, lean_deg=a)
        for k in range(900):
            f = min(1.0, k / 400)
            m.opt.gravity[:] = [0.0, 9.81 * math.sin(math.radians(a) * f), -9.81 * math.cos(math.radians(a) * f)]  # pushed toward +y, where it leans
            step(m, d)
            rec and rec(d, k, ramp=round(f, 4))
            k % 10 or ov(d, t=round(k * DT, 3), lean_deg=a)
        out_mm = {n: round(_seat_error(d, b, m.body(n).id, s)[0] * 1000, 1) for n, s in seats.items()}
        t, slide = tilt(d.xquat[b]), float(np.linalg.norm(d.xpos[b][:2] - p0))
        worst = max(out_mm, key=out_mm.get)
        mode = ("unstable" if unstable(d) else "topple" if t > 30 else "slide" if slide > 0.03
                else f"{worst} slides out" if out_mm[worst] > 10 else "holds")
        rows.append({"deg": a, "mode": mode, "block_tilt_deg": round(t, 1), "knife_out_mm": out_mm, "overlap": ov.summary(), "unstable": unstable(d)})
        recs.append(rec)
    i = next((k for k, r in enumerate(rows) if r["mode"] != "holds"), len(rows) - 1)
    first = rows[i]["deg"] if rows[i]["mode"] != "holds" else None
    out = {"kind": "tip", "engine": "mujoco", "x": list(leans), "y": [r["block_tilt_deg"] for r in rows], "x_label": "tilt-table angle (deg)",
           "y_label": "block tilt (deg)", "rows": rows, "first_failure_deg": first, "mode": rows[i]["mode"],
           "overlap": deepest(r["overlap"] for r in rows[:i + 1]), "unstable": any(r["unstable"] for r in rows),
           "note": "The table tilts so the set is pushed toward its back, the way the block and the handles lean: its least stable direction."}
    return out, recs[i] and recs[i].trace(kind="tip", lean_deg=leans[i], tilt_axis="x")

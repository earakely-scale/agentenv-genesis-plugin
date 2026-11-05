"""Validate a MuJoCo asset the way the workbench grades its own: an MJCF file, a folder holding one (scene.xml, or its
only .xml), an export zip, or a bare OBJ (given to MuJoCo as one free mesh, the naive way). Static checks read the
model; dynamic checks simulate it in MuJoCo on a floor, at its own timestep and settings, as someone loading it would.
A root body with no joint is welded to the world in MuJoCo; it is said so, and simulated with a free joint added.

    python scripts/validate_mjcf.py PATH [PATH...] --out DIR     # DIR/<name>.json and .md for each, and DIR/index.json
    python scripts/validate_mjcf.py PATH... --out DIR --web      # also DIR/<name>.glb and the settle and drop replays

Each check is pass, warn or fail, with what it measured. Fails are what a robot would hit: something inside something
else, a body that won't come to rest, mass a placeholder would give, a joint that can't reach its range.
"""

from __future__ import annotations

import argparse
import json
import math
import re
import shutil
import sys
import tempfile
import zipfile
from pathlib import Path

import mujoco
import numpy as np
import PIL.Image
import trimesh
from scipy import ndimage
from scipy.spatial import ConvexHull, cKDTree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src/agentenv_genesis/bundles/genesis-workbench/envs/genesis-workbench"))
import mj  # noqa: E402
from assets import _shrink_textures, node_frames  # noqa: E402

DEFAULT_FRICTION = (1.0, 0.005, 0.0001)
DEFAULT_SOLREF = (0.02, 1.0)


# loading

def find_mjcf(path: Path, tmp: Path) -> tuple[Path, str]:
    """The MJCF to load, and a name for the report."""
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            z.extractall(tmp)
        path = tmp
    if path.is_dir():
        depth = lambda q: (len(q.relative_to(path).parts), str(q))  # noqa: E731  the shallowest: a scene's objects have their own
        xmls = sorted(path.rglob("scene.xml"), key=depth) or sorted(path.rglob("*.xml"), key=depth)
        if not xmls:
            raise ValueError(f"no MJCF in {path}")
        return xmls[0], path.name if path != tmp else xmls[0].parent.name
    if path.suffix == ".obj":
        doc = (f'<mujoco model="{path.stem}">\n  <asset><mesh name="m" file="{path.resolve()}"/></asset>\n  <worldbody>\n'
               f'    <body name="{path.stem}"><freejoint/><geom type="mesh" mesh="m"/></body>\n  </worldbody>\n</mujoco>\n')
        out = tmp / f"{path.stem}.xml"
        out.write_text(doc)
        return out, path.stem if path.stem not in ("model", "scene") else f"{path.parent.name} (OBJ alone)"
    return path, path.stem if path.stem not in ("model", "scene") else path.parent.name


def colliding(m, g) -> bool:
    return bool(m.geom_contype[g] or m.geom_conaffinity[g])


def geom_points(m, g) -> np.ndarray:
    """A geom's points in its body's frame: a mesh's vertices, or a primitive's bounding corners."""
    if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
        return mj._to_body(m, g, mj._mesh(m, g)[0])
    s = m.geom_size[g]
    t = m.geom_type[g]
    if t == mujoco.mjtGeom.mjGEOM_SPHERE:
        h = np.array([s[0]] * 3)
    elif t == mujoco.mjtGeom.mjGEOM_BOX:
        h = s[:3]
    elif t in (mujoco.mjtGeom.mjGEOM_CYLINDER, mujoco.mjtGeom.mjGEOM_CAPSULE):
        h = np.array([s[0], s[0], s[1] + (s[0] if t == mujoco.mjtGeom.mjGEOM_CAPSULE else 0)])
    elif t == mujoco.mjtGeom.mjGEOM_ELLIPSOID:
        h = s[:3]
    else:
        return np.zeros((0, 3))
    c = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)]) * h
    return mj._to_body(m, g, c)


def geom_volume(m, g) -> float:
    """What MuJoCo collides: a mesh's convex hull, or the primitive."""
    t, s = m.geom_type[g], m.geom_size[g]
    if t == mujoco.mjtGeom.mjGEOM_MESH:
        try:
            return float(ConvexHull(mj._mesh(m, g)[0]).volume)
        except Exception:
            return 0.0
    return {mujoco.mjtGeom.mjGEOM_SPHERE: 4 / 3 * math.pi * s[0] ** 3, mujoco.mjtGeom.mjGEOM_BOX: 8 * s[0] * s[1] * s[2],
            mujoco.mjtGeom.mjGEOM_CYLINDER: math.pi * s[0] ** 2 * 2 * s[1],
            mujoco.mjtGeom.mjGEOM_CAPSULE: math.pi * s[0] ** 2 * 2 * s[1] + 4 / 3 * math.pi * s[0] ** 3,
            mujoco.mjtGeom.mjGEOM_ELLIPSOID: 4 / 3 * math.pi * s[0] * s[1] * s[2]}.get(t, 0.0)


def solid_volume(v, f) -> float:
    """The volume a mesh encloses: exact when it is watertight, else from its voxels with the closed regions filled
    (scanned meshes are rarely watertight)."""
    tm = trimesh.Trimesh(v, f, process=False)
    if tm.is_watertight and tm.volume > 0:
        return float(tm.volume)
    pitch = float(tm.extents.max()) / 60
    vox = tm.voxelized(pitch).matrix
    return float(ndimage.binary_fill_holes(ndimage.binary_closing(vox, iterations=1)).sum() * pitch ** 3)


def union_volume(m, geoms, n=40000) -> float:
    """The volume a body's colliding geoms hold together (pieces may overlap), by sampling its bounds."""
    hulls = []
    for g in geoms:
        p = geom_points(m, g)
        if len(p) >= 4:
            try:
                hulls.append(ConvexHull(p).equations)
            except Exception:
                pass
    if not hulls:
        return 0.0
    pts = np.concatenate([geom_points(m, g) for g in geoms])
    lo, hi = pts.min(0), pts.max(0)
    q = np.random.default_rng(0).uniform(lo, hi, (n, 3))
    inside = np.zeros(n, bool)
    for e in hulls:
        inside |= np.all(q @ e[:, :3].T + e[:, 3] <= 0, 1)
    return float(inside.mean() * np.prod(hi - lo))


def build(xml: Path, free_roots: bool, timestep: float | None = None, drop: float = 0.0):
    """The asset in a test world: a floor at z = 0 under its lowest colliding point (unless it has a floor of its own),
    a free joint on each root body that has no joint, and every free root lifted by `drop`."""
    spec = mujoco.MjSpec.from_file(str(xml))
    added = []
    for b in spec.worldbody.bodies:
        if free_roots and not b.joints and not b.frames:
            b.add_freejoint()
            added.append(b.name)
    m = spec.compile()
    has_floor = any(m.geom_type[g] == mujoco.mjtGeom.mjGEOM_PLANE for g in range(m.ngeom))
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    low = min((float((geom_points(m, g) @ d.xmat[m.geom_bodyid[g]].reshape(3, 3).T + d.xpos[m.geom_bodyid[g]])[:, 2].min())
               for g in range(m.ngeom) if colliding(m, g) and m.geom_bodyid[g] > 0 and len(geom_points(m, g))), default=0.0)
    if not has_floor:
        spec.worldbody.add_geom(name="validator_floor", type=mujoco.mjtGeom.mjGEOM_PLANE, size=[5, 5, 0.1], pos=[0, 0, low - 0.0005])
    if drop:
        for b in spec.worldbody.bodies:
            if any(j.type == mujoco.mjtJoint.mjJNT_FREE for j in b.joints):
                b.pos = [b.pos[0], b.pos[1], b.pos[2] + drop]
    if timestep:
        spec.option.timestep = timestep
    m = spec.compile()
    return spec, m, added, not has_floor


def free_roots(m) -> list[int]:
    return [b for b in range(1, m.nbody) if m.body_parentid[b] == 0 and m.body_jntnum[b]
            and m.jnt_type[m.body_jntadr[b]] == mujoco.mjtJoint.mjJNT_FREE]


# static checks

def components(n, touching) -> dict[int, list[int]]:
    """Groups of 0..n-1 that touch, directly or through others."""
    parent = list(range(n))

    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for i in range(n):
        for j in range(i + 1, n):
            if root(i) != root(j) and touching(i, j):
                parent[root(i)] = root(j)
    groups = {}
    for i in range(n):
        groups.setdefault(root(i), []).append(i)
    return groups


def n_(k, word) -> str:
    return f"{k} {word}" + ("" if k == 1 else "s" if not word.endswith("y") else "") if not word.endswith("y") or k == 1 else f"{k} {word[:-1]}ies"


def row(rows, id_, level, message, **data):
    rows.append({"id": id_, "level": level, "message": message, **({"data": data} if data else {})})


def static(xml: Path, m, spec, added) -> list[dict]:
    rows = []
    names = {b: m.body(b).name or f"body{b}" for b in range(m.nbody)}
    sb = {b.name: b for b in spec.bodies}
    bodies = [b for b in range(1, m.nbody) if any(colliding(m, g) for g in range(m.ngeom) if m.geom_bodyid[g] == b)]
    if added:
        row(rows, "fixed-to-world", "warn", f"{', '.join(added)} has no joint, so MuJoCo welds it to the world and it will never move. "
            "Add <freejoint/> for a free object. The dynamic checks here add one.", bodies=added)
    # where mass comes from
    implicit = [names[b] for b in bodies if names[b] in sb and not sb[names[b]].explicitinertial]
    if implicit:
        s2 = mujoco.MjSpec.from_file(str(xml))
        for g in s2.geoms:
            if g.contype == 0 and g.conaffinity == 0:
                g.density = 0.0
                g.mass = 0.0
        try:
            m2 = s2.compile()
            vis = {names[b]: 1 - float(m2.body_mass[m2.body(names[b]).id]) / max(float(m.body_mass[b]), 1e-12) for b in bodies if names[b] in implicit}
        except Exception:
            vis = {}
        heavy = {k: round(v, 3) for k, v in vis.items() if v > 0.02}
        dens = sorted({round(g.density, 1) for g in spec.geoms if (np.isnan(g.mass) or g.mass == 0)})
        row(rows, "mass-source", "fail" if heavy else "warn",
            f"{n_(len(implicit), 'body')} {'gets' if len(implicit) == 1 else 'get'} mass and inertia from {'its' if len(implicit) == 1 else 'their'} geoms (no <inertial>), "
            f"at density {', '.join(f'{x:g}' for x in dens)} kg/m³"
            + (" (MuJoCo's default, water)" if dens == [1000.0] else "")
            + (f"; the visual geoms count too: {', '.join(f'{k} {v:.0%}' for k, v in heavy.items())} of the mass is the visual mesh" if heavy else ""),
            bodies=implicit, visual_share=heavy)
    else:
        row(rows, "mass-source", "pass", "every colliding body has an explicit <inertial>")
    # density, inertia and centre of mass against the colliding volume
    dens_rows, inert_rows, com_rows = [], [], []
    for b in bodies:
        gs_ = [g for g in range(m.ngeom) if m.geom_bodyid[g] == b and colliding(m, g)]
        vol = union_volume(m, gs_)
        mass = float(m.body_mass[b])
        if vol > 0:
            rho = mass / vol
            dens_rows.append((names[b], round(rho), round(mass, 4)))
        pts = np.concatenate([geom_points(m, g) for g in gs_ if len(geom_points(m, g))] or [np.zeros((0, 3))])
        if len(pts) >= 4:
            try:
                hull = trimesh.Trimesh(pts).convex_hull
                hull.density = mass / max(hull.volume, 1e-12)
                ref = np.sort(np.linalg.eigvalsh(hull.moment_inertia))
                got = np.sort(m.body_inertia[b])
                ratio = float(got.sum() / max(ref.sum(), 1e-15))
                inert_rows.append((names[b], round(ratio, 2)))
                lo, hi = pts.min(0), pts.max(0)
                pad = (hi - lo) * 0.1
                c = m.body_ipos[b]
                if np.any(c < lo - pad) or np.any(c > hi + pad):
                    com_rows.append((names[b], np.round(c, 4).tolist()))
            except Exception:
                pass
    odd = [r for r in dens_rows if not 50 <= r[1] <= 12000]
    row(rows, "density", "fail" if any(not 10 <= r[1] <= 25000 for r in odd) else "warn" if odd else "pass",
        "mass over the volume MuJoCo collides (convex hulls, so a hollow object reads low): "
        + ", ".join(f"{n} {rho} kg/m³ ({mass} kg)" for n, rho, mass in sorted(dens_rows, key=lambda r: r[1])[:6])
        + ("; implausible: " + ", ".join(n for n, *_ in odd) if odd else ""), bodies=dens_rows)
    bad = [r for r in inert_rows if not 0.2 <= r[1] <= 5]
    row(rows, "inertia", "warn" if bad else "pass",
        "rotational inertia against a solid of the same mass filling its collision hull (1 = the same): "
        + ", ".join(f"{n} {r}×" for n, r in sorted(inert_rows, key=lambda r: abs(math.log(max(r[1], 1e-9))), reverse=True)[:6]), bodies=inert_rows)
    row(rows, "centre-of-mass", "warn" if com_rows else "pass",
        ("centre of mass outside the body: " + ", ".join(f"{n} at {c}" for n, c in com_rows)) if com_rows
        else "every body's centre of mass is within its collision geometry's bounds", bodies=com_rows)
    # contact parameters
    cg = [g for g in range(m.ngeom) if colliding(m, g) and m.geom_bodyid[g] > 0]
    dfr = [g for g in cg if np.allclose(m.geom_friction[g], DEFAULT_FRICTION)]
    row(rows, "friction", "warn" if len(dfr) == len(cg) and cg else "pass",
        f"{len(dfr)} of {len(cg)} colliding geoms have MuJoCo's default friction (1.0 sliding): " +
        ("none is set from a material" if len(dfr) == len(cg) else "the rest are set"), default=len(dfr), geoms=len(cg))
    soft = [g for g in cg if np.allclose(m.geom_solref[g], DEFAULT_SOLREF)]
    # thin pieces, and how much each mesh's hull fills in
    thin, fill = [], []
    for g in cg:
        if m.geom_type[g] != mujoco.mjtGeom.mjGEOM_MESH:
            continue
        v, f = mj._mesh(m, g)
        try:
            h = ConvexHull(v)
        except Exception:
            continue
        width = min(float((v @ n).max() - (v @ n).min()) for n in h.equations[:, :3])
        if width < 0.004:
            thin.append((names[m.geom_bodyid[g]], m.geom(g).name or f"geom{g}", round(width * 1000, 1)))
        if len(h.vertices) >= 0.9 * len(np.unique(v, axis=0)):
            continue  # already convex: its hull is itself
        solid = solid_volume(v, f)
        if solid > 0 and h.volume / solid > 1.3:
            fill.append((names[m.geom_bodyid[g]], m.geom(g).name or f"geom{g}", round(h.volume / solid, 2)))
    level = "warn" if soft and thin else "pass"
    row(rows, "contact-softness", level,
        f"{len(soft)} of {len(cg)} colliding geoms use MuJoCo's default contact softness (solref 0.02 s)"
        + (f", and {len(thin)} collision pieces are under 4 mm thick (thinnest {min(t[2] for t in thin)} mm): a soft contact lets a thin "
           "part sink further than it is thick on impact, and the piece it sank into can then push it out the far side" if level == "warn" else ""),
        default=len(soft), thin=thin[:12])
    row(rows, "hull-fill", "warn" if fill else "pass",
        ("these meshes collide as their convex hull, which holds much more than the mesh does (a cavity or slot in them won't exist "
         "for contact): " + ", ".join(f"{b}/{g} {r}×" for b, g, r in sorted(fill, key=lambda x: -x[2])[:6])) if fill
        else "no colliding mesh loses much to its convex hull", geoms=fill[:20])
    # floating pieces: colliding geoms of a body that touch nothing else of it
    float_rows = []
    for b in bodies:
        gs_ = [g for g in range(m.ngeom) if m.geom_bodyid[g] == b and colliding(m, g) and m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH]
        if len(gs_) < 2:
            continue
        clouds = []
        for g in gs_:
            v, f = mj._mesh(m, g)
            clouds.append(mj._to_body(m, g, mj._surface(v, f, 0.003, np.random.default_rng(0))))
        trees = [cKDTree(c) for c in clouds]
        groups = components(len(gs_), lambda i, j: trees[i].query(clouds[j], distance_upper_bound=0.002)[0].min() < 0.002)  # noqa: B023
        if len(groups) > 1:
            big = max(groups.values(), key=lambda g: sum(geom_volume(m, gs_[i]) for i in g))
            loose = [gs_[i] for k, g in groups.items() if g is not big for i in g]
            float_rows.append((names[b], len(loose), round(sum(geom_volume(m, g) for g in loose) / max(sum(geom_volume(m, g) for g in gs_), 1e-12), 3)))
    row(rows, "floating-pieces", "warn" if float_rows else "pass",
        ("collision pieces more than 2 mm from the rest of their body: " + ", ".join(f"{b}: {k} pieces, {s:.0%} of its volume" for b, k, s in float_rows))
        if float_rows else "every body's collision pieces touch each other", bodies=float_rows)
    # does what you see match what collides? A visual that sticks out of its collision geometry looks sunk into
    # whatever rests on it; collision that sticks out of the visual is an invisible wall
    fit = []
    rng = np.random.default_rng(1)
    for b in bodies:
        vis = [g for g in range(m.ngeom) if m.geom_bodyid[g] == b and not colliding(m, g) and m.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH]
        col = [g for g in range(m.ngeom) if m.geom_bodyid[g] == b and colliding(m, g)]
        if not vis or not col:
            continue
        hulls = []
        for g in col:
            pts = geom_points(m, g)
            if len(pts) >= 4:
                try:
                    hulls.append(ConvexHull(pts).equations)
                except Exception:
                    pass
        if not hulls:
            continue
        P = np.concatenate([mj._to_body(m, g, mj._surface(*mj._mesh(m, g), 0.01, rng)) for g in vis])
        out = np.min(np.stack([(P @ e[:, :3].T + e[:, 3]).max(1) for e in hulls]), 0)  # how far outside the nearest hull
        k = int(out.argmax())
        if out[k] > 0.005:
            up = P[k][2] - P[:, 2].mean()
            fit.append((names[b], round(float(out[k]) * 1000, 1), "above" if up > 0 else "below"))
    row(rows, "visual-fit", "warn" if fit else "pass",
        ("these visual meshes reach beyond their collision geometry: what's drawn there doesn't collide, and what rests on it looks sunk in: "
         + ", ".join(f"{n} by {mm} mm" for n, mm, _ in sorted(fit, key=lambda r: -r[1])[:6])) if fit
        else "every visual mesh lies within 5 mm of its body's collision geometry", bodies=fit)
    welded = [names[b] for b in range(1, m.nbody) if m.body_parentid[b] != 0 and m.body_jntnum[b] == 0]
    if welded:
        row(rows, "welded-bodies", "pass", f"{len(welded)} child bodies have no joint and move with their parent: {', '.join(welded[:8])}"
            + (" …" if len(welded) > 8 else ""), bodies=welded)
    joints = [m.joint(j) for j in range(m.njnt) if int(m.jnt_type[j]) in (int(mujoco.mjtJoint.mjJNT_HINGE), int(mujoco.mjtJoint.mjJNT_SLIDE))]
    if joints:
        row(rows, "joints", "pass", f"{len(joints)} hinge or slide joints: " + ", ".join(
            f"{j.name or j.id} ({'hinge' if m.jnt_type[j.id] == mujoco.mjtJoint.mjJNT_HINGE else 'slide'}"
            + (f", range {np.round(m.jnt_range[j.id], 3).tolist()}" if m.jnt_limited[j.id] else ", no limit")
            + f", damping {m.dof_damping[m.jnt_dofadr[j.id]]:g})" for j in joints[:8]))
    return rows


# dynamic checks

def run(m, seconds, ov=True, rec_every=0, **where):
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    o = mj.Overlap(m, surface="collision") if ov else None
    o and o(d, t=0.0, **where)
    rec = mj.Recorder(m, rec_every) if rec_every else None
    n = int(round(seconds / m.opt.timestep))
    every = max(1, int(round(0.02 / m.opt.timestep)))
    late = n - int(round(0.2 / m.opt.timestep))
    for k in range(n):
        if k == late:
            run.before = (d.xpos.copy(), d.xquat.copy())
        mujoco.mj_step(m, d)
        if rec and k % every == 0:
            rec.frames.append(np.round(np.concatenate([d.xpos[rec.ids], d.xquat[rec.ids]], 1), 5).tolist())
            rec.t.append(round(k * m.opt.timestep, 3))
        if o and k % every == 0:
            o(d, t=round(k * m.opt.timestep, 3), **where)
    o and o(d, t=round(n * m.opt.timestep, 3), **where)
    return d, (o.summary() if o else None), rec


def dynamic(xml: Path, free: bool) -> tuple[list[dict], dict]:
    rows, traces = [], {}
    spec, m, _, _ = build(xml, free)
    roots = free_roots(m)
    d0 = mujoco.MjData(m)
    mujoco.mj_forward(m, d0)
    o0 = mj.Overlap(m, surface="collision")
    o0(d0, t=0.0)
    s0 = o0.summary()
    bodies0 = [r for r in s0["pairs"] if r["into"] != "floor"]
    row(rows, "starts-apart", "fail" if bodies0 and bodies0[0]["mm"] > 0.5 else "pass",
        (f"at the start, {bodies0[0]['body']} is {bodies0[0]['mm']} mm inside {bodies0[0]['into']}" + (f" (and {len(bodies0) - 1} more pairs overlap)" if len(bodies0) > 1 else ""))
        if bodies0 and bodies0[0]["mm"] > 0.5 else "no body starts inside another", pairs=bodies0[:10])
    # settle
    d, ov, rec = run(m, 2.0, rec_every=1)
    traces["settle"] = rec.trace(kind="rest")
    # at rest means nothing moves over the last 0.2 s; a part that shakes in place is told apart
    p0, q0 = run.before
    moving = [b for b in range(1, m.nbody) if m.body_weldid[b] != 0]
    drift = max((float(np.linalg.norm(d.xpos[b] - p0[b])) * 1000 for b in moving), default=0.0)
    turn = max((mj.rotation_deg(d.xquat[b], q0[b]) for b in moving), default=0.0)
    who = max(moving, key=lambda b: float(np.linalg.norm(d.xpos[b] - p0[b])) * 1000 + mj.rotation_deg(d.xquat[b], q0[b]), default=None)
    speed = float(np.abs(d.qvel).max()) if m.nv else 0.0
    shaker = m.body(m.jnt_bodyid[m.dof_jntid[int(np.abs(d.qvel).argmax())]]).name if m.nv else ""
    bad = mj.unstable(d)
    rest = drift < 1.0 and turn < 1.0
    row(rows, "settles", "fail" if bad or not rest else "warn" if speed > 0.05 else "pass",
        "MuJoCo went unstable and reset the state" if bad else
        (f"after 2 s on the floor nothing moves more than {drift:.1f} mm or {turn:.1f}° in 0.2 s" if rest else
         f"after 2 s {m.body(who).name} is still moving: {drift:.1f} mm and {turn:.1f}° in the last 0.2 s")
        + (f"; {shaker} shakes in place at {speed:.2f} (m/s or rad/s) without going anywhere" if rest and speed > 0.05 else ""),
        drift_mm=round(drift, 2), turn_deg=round(turn, 2), max_speed=round(speed, 4))
    end_b, end_f, peak = ov["end"]["bodies"], ov["end"]["floor"], ov["pairs"][:1]
    say = lambda r: f"{r['body']} {r['mm']} mm into {r['into']} at {r['t']} s"  # noqa: E731
    ok = (end_b is None or end_b["mm"] <= 0.5) and (end_f is None or end_f["mm"] <= 1.0) and all(r["mm"] <= 3.0 for r in peak)
    row(rows, "no-overlap", "pass" if ok else "fail",
        f"settling: deepest at any moment {say(peak[0]) if peak else 'none'}; once settled, between bodies {say(end_b) if end_b else 'none'}, "
        f"into the floor {say(end_f) if end_f else 'none'} (allowed: 0.5 mm between bodies and 1 mm into the floor at rest, 3 mm at any moment)",
        overlap=ov)
    # timestep: the same settle at half the step
    if roots:
        _, m2, _, _ = build(xml, free, timestep=m.opt.timestep / 2)
        d2, _, _ = run(m2, 2.0, ov=False)
        far = max(roots, key=lambda b: float(np.linalg.norm(d.xpos[b] - d2.xpos[b])) * 1000 + mj.rotation_deg(d.xquat[b], d2.xquat[b]))
        dp = float(np.linalg.norm(d.xpos[far] - d2.xpos[far])) * 1000
        dq = mj.rotation_deg(d.xquat[far], d2.xquat[far])
        row(rows, "timestep", "warn" if dp > 3 or dq > 3 else "pass",
            f"settled at {m.opt.timestep * 1000:g} ms and at {m.opt.timestep * 500:g} ms steps, "
            + (f"{m.body(far).name} ends {dp:.1f} mm and {dq:.1f}° apart" if len(roots) > 1 else f"it ends {dp:.1f} mm and {dq:.1f}° apart"),
            body=m.body(far).name, mm=round(dp, 2), deg=round(dq, 2))
        # tipping margin from where it stands
        floor = [g for g in range(m.ngeom) if m.geom_type[g] == mujoco.mjtGeom.mjGEOM_PLANE]
        on_floor = {}
        for i in range(d.ncon):
            c = d.contact[i]
            if c.geom1 in floor or c.geom2 in floor:
                on_floor.setdefault(int(m.body_rootid[m.geom_bodyid[c.geom1 if c.geom2 in floor else c.geom2]]), []).append(c.pos[:2].copy())
        standing = [b for b in roots if b in on_floor]
        riders = [b for b in roots if b not in on_floor]
        fz = min(float(d.geom_xpos[g][2]) for g in floor)
        for b in standing[:4]:
            group = [b] + (riders if len(standing) == 1 else [])  # what rests on it moves with it
            mass = sum(float(m.body_subtreemass[x]) for x in group)
            com = sum(d.subtree_com[x] * float(m.body_subtreemass[x]) for x in group) / mass
            who = m.body(b).name + (f" with the {len(riders)} bodies resting on it" if len(group) > 1 else "")
            # its footprint: the colliding points of its own geoms within 1 mm of the floor
            feet = []
            for g in range(m.ngeom):
                if colliding(m, g) and m.body_rootid[m.geom_bodyid[g]] == b:
                    w = geom_points(m, g) @ d.xmat[m.geom_bodyid[g]].reshape(3, 3).T + d.xpos[m.geom_bodyid[g]]
                    feet.append(w[w[:, 2] < fz + 0.001][:, :2])
            P = np.concatenate(feet) if feet else np.zeros((0, 2))
            try:
                hull = ConvexHull(P)
            except Exception:
                row(rows, "tip-margin", "warn", f"{who} touches the floor along a line or at a point: it doesn't stand on its own", body=m.body(b).name)
                continue
            # the signed distance from its centre of mass to the footprint's nearest edge (negative: outside it)
            dist = min(-(e[:2] @ com[:2] + e[2]) for e in hull.equations)
            ang = math.degrees(math.atan2(dist, float(com[2]) - fz))
            row(rows, "tip-margin", "fail" if ang < 0 else "warn" if ang < 8 else "pass",
                f"{who} can tilt {ang:.1f}° toward its least stable side before its centre of mass passes the edge of its footprint",
                body=m.body(b).name, deg=round(ang, 1))
        # drop from 10 cm: an object on its own, not a scene with furniture in it
        fixed_parts = any(colliding(m, g) and m.geom_bodyid[g] > 0 and m.body_rootid[m.geom_bodyid[g]] not in roots for g in range(m.ngeom))
    if roots and not fixed_parts:
        _, md, _, _ = build(xml, free, drop=0.10)
        dd = mujoco.MjData(md)
        mujoco.mj_forward(md, dd)
        rest_z = {b: float(d.subtree_com[b][2]) for b in roots}
        od = mj.Overlap(md, surface="collision")
        hit, bounce = {b: False for b in roots}, {b: 0.0 for b in roots}
        n = int(round(1.5 / md.opt.timestep))
        every = max(1, int(round(0.01 / md.opt.timestep)))
        rec2 = mj.Recorder(md, 1)
        for k in range(n):
            mujoco.mj_step(md, dd)
            for b in roots:
                z = float(dd.subtree_com[b][2]) - rest_z[b]
                if not hit[b] and dd.cvel[b][5] > 0:
                    hit[b] = True
                if hit[b]:
                    bounce[b] = max(bounce[b], z)
            if k % every == 0:
                od(dd, t=round(k * md.opt.timestep, 3))
            if k % (2 * every) == 0:
                rec2.frames.append(np.round(np.concatenate([dd.xpos[rec2.ids], dd.xquat[rec2.ids]], 1), 5).tolist())
                rec2.t.append(round(k * md.opt.timestep, 3))
        traces["drop"] = rec2.trace(kind="drop")
        so = od.summary()
        floor_pen = so["floor"]["mm"] if so["floor"] else 0.0
        worst = max(bounce.values()) / 0.10
        soft_ = all(np.allclose(md.geom_solref[g], DEFAULT_SOLREF) for g in range(md.ngeom) if colliding(md, g) and md.geom_bodyid[g] > 0)
        row(rows, "drop", "fail" if mj.unstable(dd) or worst > 0.5 else "warn" if worst > 0.3 or floor_pen > 3 else "pass",
            f"dropped from 10 cm it bounces back {worst:.0%} of the way and sinks {floor_pen:.1f} mm into the floor on impact"
            + (" (its geoms keep MuJoCo's soft default contacts; solref around 0.003 with a 1 ms step keeps it under 2 mm)" if floor_pen > 3 and soft_ else ""),
            bounce=round(worst, 3), floor_mm=floor_pen)
    # joints: do they hold under gravity, and reach their range when driven?
    jrows = joints(xml, free, m, d)
    rows.extend(jrows)
    return rows, traces


def joints(xml, free, m, d_settled) -> list[dict]:
    rows = []
    js = [j for j in range(m.njnt) if int(m.jnt_type[j]) in (int(mujoco.mjtJoint.mjJNT_HINGE), int(mujoco.mjtJoint.mjJNT_SLIDE))]
    if not js:
        return rows
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    q0 = d.qpos.copy()
    for _ in range(int(round(2.0 / m.opt.timestep))):
        mujoco.mj_step(m, d)
    drift = []
    for j in js:
        a = m.jnt_qposadr[j]
        dq = float(d.qpos[a] - q0[a])
        hinge = m.jnt_type[j] == mujoco.mjtJoint.mjJNT_HINGE
        big = abs(math.degrees(dq)) > 5 if hinge else abs(dq) > 0.005
        if big:
            drift.append((m.joint(j).name or f"joint{j}", round(math.degrees(dq), 1) if hinge else round(dq * 1000, 1), "deg" if hinge else "mm"))
    row(rows, "joints-hold", "warn" if drift else "pass",
        ("left alone for 2 s, these joints move under gravity (no damping or friction holds them): " + ", ".join(f"{n} {v} {u}" for n, v, u in drift))
        if drift else f"left alone for 2 s, none of the {len(js)} joints moves more than 5° or 5 mm", joints=drift)
    # drive each limited joint to each end with a stiff servo on its own dof, the others held where they start
    reach = []
    M0 = m.dof_M0.copy()
    for j in js:
        if not m.jnt_limited[j]:
            continue
        a, v = m.jnt_qposadr[j], m.jnt_dofadr[j]
        kp = float(M0[v]) * (2 * math.pi * 3) ** 2
        kd = 2 * math.sqrt(kp * float(M0[v]))
        lo, hi = m.jnt_range[j]
        for target in (hi, lo):
            dd = mujoco.MjData(m)
            dd.qpos[:] = q0
            mujoco.mj_forward(m, dd)
            ov = mj.Overlap(m, surface="collision")
            n = int(round(1.5 / m.opt.timestep))
            for k in range(n):
                s = min(1.0, k / (0.8 * n))
                goal = q0[a] + (target - q0[a]) * s
                dd.qfrc_applied[:] = 0
                dd.qfrc_applied[v] = kp * (goal - dd.qpos[a]) - kd * dd.qvel[v]
                mujoco.mj_step(m, dd)
                if k % max(1, int(round(0.02 / m.opt.timestep))) == 0:
                    ov(dd, t=round(k * m.opt.timestep, 3))
            span = target - q0[a]
            frac = 1.0 if abs(span) < 1e-9 else float((dd.qpos[a] - q0[a]) / span)
            pairs = [r for r in ov.summary()["pairs"] if r["into"] != "floor"]
            reach.append((m.joint(j).name or f"joint{j}", "upper" if target == hi else "lower", round(frac, 2), pairs[0] if pairs else None))
    jam = [r for r in reach if r[2] < 0.9]
    if reach:
        row(rows, "joints-reach", "fail" if jam else "pass",
            ("driven to the ends of their range, these stop short: " + ", ".join(
                f"{n} {end} end {f:.0%}" + (f" ({p['body']} {p['mm']} mm into {p['into']})" if p else "") for n, end, f, p in jam))
            if jam else f"each of the {len(reach) // 2} limited joints reaches both ends of its range when driven", reach=reach)
    return rows


def validate(path: Path, keep_fixed: bool = False) -> dict:
    tmp = Path(tempfile.mkdtemp(prefix="validate-"))
    try:
        xml, name = find_mjcf(path, tmp)
        out = {"name": name, "path": path.name, "mjcf": xml.name, "mujoco": mujoco.__version__}
        try:
            spec, m, added, floor_added = build(xml, not keep_fixed)
        except Exception as e:
            out.update(checks=[{"id": "loads", "level": "fail", "message": f"MuJoCo can't load it: {str(e)[:300]}"}], verdict="fail")
            return out
        out.update(bodies=m.nbody - 1, geoms=int(m.ngeom), joints=int(m.njnt), timestep=float(m.opt.timestep),
                   mass_kg=round(float(sum(m.body_mass[1:])), 4), floor_added=floor_added)
        checks = [{"id": "loads", "level": "pass", "message": f"loads in MuJoCo {mujoco.__version__}: {n_(m.nbody - 1, 'body')}, {n_(m.ngeom, 'geom')}, "
                                                              f"{n_(m.njnt, 'joint')}" + (" (with the free joints added here)" if added else "")}]
        checks += static(xml, m, spec, added)
        if keep_fixed and any(m.body_parentid[b] == 0 and m.body_jntnum[b] == 0 for b in range(1, m.nbody)):
            checks.append({"id": "fixed-to-world", "level": "pass", "message": "its root is fixed to the world, as furniture is (kept with --keep-fixed)"})
        dyn, traces = dynamic(xml, not keep_fixed)
        checks += dyn
        out["checks"] = checks
        out["traces"] = traces
        out["verdict"] = "fail" if any(c["level"] == "fail" for c in checks) else "warn" if any(c["level"] == "warn" for c in checks) else "pass"
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _mat(q):
    R = np.zeros(9)
    mujoco.mju_quat2Mat(R, q)
    return R.reshape(3, 3)


def _mjcf_texture(m, spec, root: Path, g):
    """The image an MJCF material gives a geom, if it has one with a file."""
    try:
        if m.geom_matid[g] < 0:
            return None
        names = spec.material(m.material(m.geom_matid[g]).name).textures
        tex = next((t for t in names if t), None)
        if not tex:
            return None
        f = root / (spec.texturedir or "") / spec.texture(tex).file
        return PIL.Image.open(f).convert("RGB") if f.exists() else None
    except Exception:
        return None


def geom_mesh(m, spec, root: Path, g) -> trimesh.Trimesh | None:
    """A geom as a mesh in its body's frame: a mesh geom from its own file when it loads (keeping its texture), placed as
    MuJoCo placed it; a primitive from its size."""
    t, size = m.geom_type[g], m.geom_size[g]
    if t == mujoco.mjtGeom.mjGEOM_MESH:
        k = m.geom_dataid[g]
        tm = None
        try:
            sm = spec.mesh(m.mesh(k).name)
            f = root / (spec.meshdir or "") / sm.file
            if f.with_suffix(".glb").exists():  # a converted mesh keeps its textured original beside it
                f = f.with_suffix(".glb")
            if f.suffix.lower() in (".obj", ".stl", ".ply", ".glb") and f.exists():
                tm = trimesh.load(f, force="mesh", process=False)
                tm.vertices = (np.asarray(tm.vertices) * np.asarray(sm.scale) - m.mesh_pos[k]) @ _mat(m.mesh_quat[k])
        except Exception:
            tm = None
        if tm is None or len(tm.vertices) == 0:
            v, fc = mj._mesh(m, g)
            tm = trimesh.Trimesh(v, fc, process=False)
    elif t == mujoco.mjtGeom.mjGEOM_BOX:
        tm = trimesh.creation.box(extents=2 * size[:3])
    elif t == mujoco.mjtGeom.mjGEOM_SPHERE:
        tm = trimesh.creation.icosphere(subdivisions=2, radius=size[0])
    elif t == mujoco.mjtGeom.mjGEOM_CYLINDER:
        tm = trimesh.creation.cylinder(radius=size[0], height=2 * size[1])
    elif t == mujoco.mjtGeom.mjGEOM_CAPSULE:
        tm = trimesh.creation.capsule(radius=size[0], height=2 * size[1])
    elif t == mujoco.mjtGeom.mjGEOM_ELLIPSOID:
        tm = trimesh.creation.icosphere(subdivisions=2, radius=1.0)
        tm.vertices = tm.vertices * size[:3]
    else:
        return None
    tm.vertices = np.asarray(tm.vertices) @ _mat(m.geom_quat[g]).T + m.geom_pos[g]
    image = _mjcf_texture(m, spec, root, g)
    uv = getattr(tm.visual, "uv", None)
    if image is not None and uv is not None and len(uv) == len(tm.vertices):
        tm.visual = trimesh.visual.TextureVisuals(uv=uv, image=image)
    if not isinstance(tm.visual, trimesh.visual.TextureVisuals) or getattr(tm.visual.material, "image", None) is None \
            or tm.visual.material.image.size[0] <= 4:
        rgba = m.mat_rgba[m.geom_matid[g]] if m.geom_matid[g] >= 0 else m.geom_rgba[g]
        rgba = (0.72, 0.72, 0.7, 1.0) if np.allclose(rgba, (0.5, 0.5, 0.5, 1)) else rgba
        tm.visual = trimesh.visual.ColorVisuals(tm, face_colors=[int(c * 255) for c in rgba])
    else:
        _shrink_textures(tm, 512)
    return tm


def web_scene(xml: Path, m) -> tuple[bytes, dict]:
    """The asset for the browser: one GLB node per geom (its visual geoms, or its colliding ones where a body has none),
    each in its body's frame, and where each body starts."""
    spec = mujoco.MjSpec.from_file(str(xml))
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    plane = mujoco.mjtGeom.mjGEOM_PLANE
    vis = [g for g in range(m.ngeom) if not colliding(m, g) and m.geom_bodyid[g] > 0 and m.geom_type[g] != plane]
    has_vis = {int(m.geom_bodyid[g]) for g in vis}
    show = vis + [g for g in range(m.ngeom) if colliding(m, g) and m.geom_bodyid[g] > 0 and m.geom_type[g] != plane and m.geom_bodyid[g] not in has_vis]
    scene, geoms, pts = trimesh.Scene(), [], []
    for g in show:
        tm = geom_mesh(m, spec, xml.parent, g)
        if tm is None:
            continue
        i = len(geoms)
        b = int(m.geom_bodyid[g])
        scene.add_geometry(tm, node_name=f"v{i}", geom_name=f"g{i}")
        pose = np.concatenate([d.xpos[b], d.xquat[b]]).round(6).tolist()
        geoms.append({"i": i, "link": m.body(b).name, "part": f"{m.body(b).name}.{m.geom(g).name or g}", "pose": pose})
        pts.append(np.asarray(tm.vertices) @ d.xmat[b].reshape(3, 3).T + d.xpos[b])
    v = np.concatenate(pts)
    meta = {"kind": "set", "engine": "mujoco", "geoms": geoms, "lift": 0.0, "scale": 1.0, "mass_kg": round(float(sum(m.body_mass[1:])), 4),
            "bounds": np.round([v.min(0), v.max(0)], 4).tolist(), "size_m": np.round(v.max(0) - v.min(0), 4).tolist()}
    return scene.export(file_type="glb"), meta


def markdown(r: dict) -> str:
    icon = {"pass": "✓", "warn": "⚠", "fail": "✗"}
    lines = [f"# {r['name']}: {r['verdict']}", "", f"`{r['path']}` ({r['mjcf']}), MuJoCo {r['mujoco']}"
             + (f", {r['bodies']} bodies, {r.get('mass_kg')} kg, timestep {r.get('timestep', 0) * 1000:g} ms" if "bodies" in r else ""), "",
             "| | Check | What it found |", "|---|---|---|"]
    for c in r["checks"]:
        lines.append(f"| {icon[c['level']]} | {c['id']} | {c['message'].replace('|', '/')} |")
    return "\n".join(lines) + "\n"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("paths", nargs="+")
    ap.add_argument("--out", default="validation")
    ap.add_argument("--traces", action="store_true", help="keep the settle and drop traces in the JSON")
    ap.add_argument("--web", action="store_true", help="write each asset's GLB and its replays, mapped to the GLB's nodes, for the Space")
    ap.add_argument("--keep-fixed", action="store_true", help="leave a root body with no joint welded to the world (furniture)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    index = []
    for p in a.paths:
        r = validate(Path(p), a.keep_fixed)
        slug = re.sub(r"\W+", "_", r["name"]).strip("_")
        if a.web and r.get("traces"):
            tmp = Path(tempfile.mkdtemp(prefix="validate-web-"))
            try:
                xml, _ = find_mjcf(Path(p), tmp)
                _, m, _, _ = build(xml, not a.keep_fixed)
                glb, meta = web_scene(xml, m)
                (out / f"{slug}.glb").write_bytes(glb)
                (out / f"{slug}.scene.json").write_text(json.dumps(meta))
                r["traces"] = {k: {**node_frames(t, meta["geoms"]), "kind": t.get("kind", k)} for k, t in r["traces"].items()}
                r["scene"] = f"{slug}.glb"
            finally:
                shutil.rmtree(tmp, ignore_errors=True)
        elif not a.traces:
            r.pop("traces", None)
        (out / f"{slug}.json").write_text(json.dumps(r, default=float, indent=1))
        (out / f"{slug}.md").write_text(markdown(r))
        index.append({"name": r["name"], "file": f"{slug}.json", "verdict": r["verdict"],
                      "fails": [c["id"] for c in r["checks"] if c["level"] == "fail"], "warns": [c["id"] for c in r["checks"] if c["level"] == "warn"]})
        print(f"{r['verdict']:5s} {r['name']}: fail {index[-1]['fails']} warn {index[-1]['warns']}", flush=True)
    (out / "index.json").write_text(json.dumps(index, indent=1))


if __name__ == "__main__":
    main()

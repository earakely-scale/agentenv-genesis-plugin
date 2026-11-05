"""Build a knife-block set (a block with slots and the knives that sit in them) as a workbench asset: one asset.json
describing every body, its parts, materials and seated pose, the slots, and the OBJ meshes it names.

The block's collision is convex pieces that leave its slots open: the block's outer hull, cut in the slots' frame into
cells around the slots (MuJoCo collides each mesh as its convex hull, so a slotted block can't be one mesh). Each knife
collides as one convex hull per part (blade, bolster, handle).

    python scripts/build_knife_block.py --procedural OUT    # our own procedural set
    python scripts/build_knife_block.py --glb SET.glb --spec physical_properties.json OUT   # a set from its meshes and spec
"""

import argparse
import json
import math
import os

import numpy as np
import trimesh
from scipy.spatial import ConvexHull, HalfspaceIntersection

MATERIALS = {"beech_wood": {"density": 710.0, "friction": 0.45, "rgba": [0.80, 0.62, 0.40, 1]},
             "stainless_steel": {"density": 7700.0, "friction": 0.35, "rgba": [0.74, 0.76, 0.78, 1]},
             "pom": {"density": 1410.0, "friction": 0.35, "rgba": [0.08, 0.08, 0.09, 1]},
             "rubber": {"density": 1100.0, "friction": 0.9, "rgba": [0.12, 0.12, 0.12, 1]}}
TILT = math.radians(18)
AXIS = np.array([0.0, math.sin(TILT), math.cos(TILT)])     # slots point out of the block along this
EV = np.array([0.0, math.cos(TILT), -math.sin(TILT)])      # across the slot, in the block's side plane
EX = np.array([1.0, 0.0, 0.0])                             # slot width direction


def to_slot(p):
    return np.stack([p @ EX, p @ EV, p @ AXIS], -1)


def from_slot(q):
    q = np.asarray(q)
    return q[..., :1] * EX + q[..., 1:2] * EV + q[..., 2:3] * AXIS


def polytope(halfspaces, interior):
    """The convex region where every halfspace [a, b] has a.x + b <= 0, as a mesh; None when it's empty or degenerate."""
    try:
        hs = HalfspaceIntersection(np.asarray(halfspaces), np.asarray(interior, dtype=float))
        pts = hs.intersections
        if len(pts) < 4:
            return None
        h = trimesh.Trimesh(pts).convex_hull
        return h if h.volume > 1e-9 else None
    except Exception:
        return None


def slot_cells(hull_halfspaces, slots, wtop, x_range, v_range):
    """The block's solid as convex pieces: cells of the slots' frame around each slot, each cut by the block's outer hull."""
    xs = sorted({x_range[0], x_range[1], *[s["x"] - s["width"] / 2 for s in slots], *[s["x"] + s["width"] / 2 for s in slots]})
    pieces = []
    for x0, x1 in zip(xs, xs[1:]):
        xm = (x0 + x1) / 2
        here = [s for s in slots if abs(xm - s["x"]) < s["width"] / 2]
        vs = sorted({v_range[0], v_range[1], *[s["v0"] for s in here], *[s["v0"] + s["length"] for s in here]})
        for v0, v1 in zip(vs, vs[1:]):
            vm = (v0 + v1) / 2
            inside = [s for s in here if s["v0"] < vm < s["v0"] + s["length"]]
            top = wtop - inside[0]["depth"] if inside else wtop
            # box in the slot frame: x0<=x<=x1, v0<=v<=v1, w<=top, written as world-frame halfspaces
            box = [np.r_[-EX, x0], np.r_[EX, -x1], np.r_[-EV, v0], np.r_[EV, -v1], np.r_[AXIS, -top]]
            # an interior point: the cell's middle at a height well inside the block
            for w in np.linspace(top - 0.004, -0.05, 40):
                c = from_slot([xm, vm, w])
                if all(h[:3] @ c + h[3] < -1e-5 for h in hull_halfspaces + box):
                    piece = polytope(hull_halfspaces + box, c)
                    if piece is not None:
                        pieces.append(piece)
                    break
    return pieces


def wall_pieces(hull_halfspaces, slots, wtop, x_range, v_range):
    """The block's solid as overlapping convex pieces, each as large as the slots allow: every box of the slots' grid
    that is solid up to some height (a whole wall, the slab between rows, the wood under the slots), cut by the outer
    hull. MuJoCo pushes a body out of each piece the shortest way. A blade that sinks into a piece only a few mm wide is
    pushed out of its side, through the wall, while one that sinks into a whole wall is pushed back into its slot."""
    xs = sorted({x_range[0], x_range[1], *[s["x"] - s["width"] / 2 for s in slots], *[s["x"] + s["width"] / 2 for s in slots]})
    vs = sorted({v_range[0], v_range[1], *[s["v0"] for s in slots], *[s["v0"] + s["length"] for s in slots]})
    top = np.full((len(xs) - 1, len(vs) - 1), wtop)
    for i, (x0, x1) in enumerate(zip(xs, xs[1:])):
        for j, (v0, v1) in enumerate(zip(vs, vs[1:])):
            xm, vm = (x0 + x1) / 2, (v0 + v1) / 2
            for s in slots:
                if abs(xm - s["x"]) < s["width"] / 2 and s["v0"] < vm < s["v0"] + s["length"]:
                    top[i, j] = wtop - s["depth"]
    boxes = set()
    for h in sorted(set(top.ravel())):
        solid = top >= h - 1e-9
        for i, j in zip(*np.nonzero(solid)):
            for first in (0, 1):
                r = [i, i, j, j]
                for axis in (first, 1 - first):
                    lo, hi = (0, 1) if axis == 0 else (2, 3)
                    while r[lo] > 0 and solid[(r[lo] - 1, slice(r[2], r[3] + 1)) if axis == 0 else (slice(r[0], r[1] + 1), r[lo] - 1)].all():
                        r[lo] -= 1
                    n = solid.shape[axis]
                    while r[hi] < n - 1 and solid[(r[hi] + 1, slice(r[2], r[3] + 1)) if axis == 0 else (slice(r[0], r[1] + 1), r[hi] + 1)].all():
                        r[hi] += 1
                boxes.add((*r, float(h)))
    # drop each box whose wood the others already hold to its height, the narrowest first
    width = lambda b: min(xs[b[1] + 1] - xs[b[0]], vs[b[3] + 1] - vs[b[2]])
    boxes = sorted(boxes, key=width)
    for b in list(boxes):
        rest = [o for o in boxes if o != b]
        held = lambda i, j: max((o[4] for o in rest if o[0] <= i <= o[1] and o[2] <= j <= o[3]), default=-1.0)
        if all(held(i, j) >= b[4] - 1e-9 for i in range(b[0], b[1] + 1) for j in range(b[2], b[3] + 1)):
            boxes.remove(b)
    pieces = []
    for i0, i1, j0, j1, h in sorted(boxes):
        x0, x1, v0, v1 = xs[i0], xs[i1 + 1], vs[j0], vs[j1 + 1]
        box = [np.r_[-EX, x0], np.r_[EX, -x1], np.r_[-EV, v0], np.r_[EV, -v1], np.r_[AXIS, -h]]
        for w in np.linspace(h - 0.004, -0.05, 40):
            c = from_slot([(x0 + x1) / 2, (v0 + v1) / 2, w])
            if all(hh[:3] @ c + hh[3] < -1e-5 for hh in hull_halfspaces + box):
                piece = polytope(hull_halfspaces + box, c)
                if piece is not None:
                    pieces.append(piece)
                break
    return pieces


def hull_halfspaces(points):
    h = ConvexHull(points)
    return [np.asarray(e) for e in h.equations]


def mass_props(parts):
    """Mass, centre of mass and inertia about it (body frame) of (mesh, density) parts."""
    total, mom, comps = 0.0, np.zeros(3), []
    for m, rho in parts:
        mm = m.copy()
        mm.density = rho
        comps.append(mm)
        total += mm.mass
        mom += mm.mass * mm.center_mass
    com = mom / total
    inertia = np.zeros((3, 3))
    for mm in comps:
        d = mm.center_mass - com
        inertia += mm.moment_inertia + mm.mass * (np.dot(d, d) * np.eye(3) - np.outer(d, d))
    return total, com, inertia


def fullinertia(I):
    return [float(I[0, 0]), float(I[1, 1]), float(I[2, 2]), float(I[0, 1]), float(I[0, 2]), float(I[1, 2])]


def quat_from_matrix(R):
    return trimesh.transformations.quaternion_from_matrix(np.vstack([np.c_[R, np.zeros(3)], [0, 0, 0, 1]]))  # w, x, y, z


def extrude(poly, thickness):
    """A convex polygon in the knife's x-y plane, extruded symmetrically along z."""
    poly = np.asarray(poly)
    pts = np.vstack([np.c_[poly, np.full(len(poly), -thickness / 2)], np.c_[poly, np.full(len(poly), thickness / 2)]])
    return trimesh.Trimesh(pts).convex_hull


# Our own set: six knives, a slanted beech block, one row of three slots at the front and three behind.
KNIVES = {
    "chef_20cm": {"label": "Chef's knife 20 cm", "blade": [(0, 0), (0.12, 0), (0.2, 0.007), (0.16, 0.026), (0.1, 0.04), (0.03, 0.048), (0, 0.048)],
                  "t": 0.0028, "handle": (0.125, 0.026, 0.021), "slot": {"x": -0.031, "row": 0, "width": 0.0045, "length": 0.052, "depth": 0.208}},
    "bread_20cm": {"label": "Bread knife 20 cm", "blade": [(0, 0), (0.2, 0.004), (0.2, 0.022), (0.19, 0.03), (0, 0.03)],
                   "t": 0.002, "handle": (0.125, 0.025, 0.02), "slot": {"x": 0.0, "row": 0, "width": 0.0036, "length": 0.034, "depth": 0.208}},
    "santoku_17cm": {"label": "Santoku 17 cm", "blade": [(0, 0), (0.11, 0), (0.17, 0.034), (0.16, 0.044), (0, 0.044)],
                     "t": 0.0022, "handle": (0.12, 0.024, 0.02), "slot": {"x": 0.031, "row": 0, "width": 0.004, "length": 0.048, "depth": 0.178}},
    "utility_13cm": {"label": "Utility knife 13 cm", "blade": [(0, 0), (0.11, 0), (0.13, 0.006), (0.08, 0.02), (0, 0.023)],
                     "t": 0.0016, "handle": (0.1, 0.02, 0.016), "slot": {"x": -0.024, "row": 1, "width": 0.0032, "length": 0.027, "depth": 0.138}},
    "boning_15cm": {"label": "Boning knife 15 cm", "blade": [(0, 0), (0.15, 0.004), (0.09, 0.015), (0.03, 0.022), (0, 0.022)],
                    "t": 0.002, "handle": (0.115, 0.022, 0.0185), "slot": {"x": 0.0, "row": 1, "width": 0.0035, "length": 0.025, "depth": 0.158}},
    "paring_9cm": {"label": "Paring knife 9 cm", "blade": [(0, 0), (0.075, 0), (0.09, 0.006), (0.05, 0.018), (0, 0.02)],
                   "t": 0.0015, "handle": (0.095, 0.02, 0.0165), "slot": {"x": 0.024, "row": 1, "width": 0.003, "length": 0.024, "depth": 0.098}},
}
BOLSTER = 0.011
ROWS_V0 = [-0.058, 0.004]                                   # where each row's slots start across the block (slot frame)
BLOCK = {"x": (-0.061, 0.061), "v": (-0.066, 0.074), "wtop": 0.222, "floor": 0.003}  # deep enough behind that it doesn't tip back


def procedural(out):
    os.makedirs(out, exist_ok=True)
    wtop = BLOCK["wtop"]
    # the block's outer hull: the slanted prism above the pads
    hs_outer = [np.r_[-EX, BLOCK["x"][0]], np.r_[EX, -BLOCK["x"][1]], np.r_[-EV, BLOCK["v"][0]], np.r_[EV, -BLOCK["v"][1]],
                np.r_[AXIS, -wtop], np.r_[-np.array([0, 0, 1.0]), BLOCK["floor"]]]
    slots = []
    for name, k in KNIVES.items():
        s = k["slot"]
        slots.append({"name": name, "x": s["x"], "v0": ROWS_V0[s["row"]], "width": s["width"], "length": s["length"], "depth": s["depth"]})
    cells = slot_cells(hs_outer, slots, wtop, BLOCK["x"], BLOCK["v"])
    pieces = wall_pieces(hs_outer, slots, wtop, BLOCK["x"], BLOCK["v"])
    pads = [trimesh.creation.box((0.014, 0.014, BLOCK["floor"]), trimesh.transformations.translation_matrix(
        (sx * 0.048, sy, BLOCK["floor"] / 2))) for sx in (-1, 1) for sy in (-0.056, 0.064)]
    spec = assemble(out, pieces, trimesh.util.concatenate(cells), trimesh.util.concatenate(pads), slots, wtop, own_knives=True, mass_pieces=cells)
    spec.update({"id": "knife_block", "label": "knife block",
                 "description": "A slanted beech knife block on rubber pads, with six knives: chef, bread, santoku, utility, boning, paring.",
                 "credit": "Procedural geometry, generated by scripts/build_knife_block.py"})
    return spec


def assemble(out, block_pieces, block_visual, pads, slots, wtop, own_knives, knife_meshes=None, spec_in=None, mass_pieces=None):
    """Write the meshes and asset.json for a block, its pads and its knives. The block collides as `block_pieces`, which
    may overlap; its mass comes from `mass_pieces`, which don't."""
    bodies = []
    files = []
    for i, p in enumerate(block_pieces):
        fn = f"knife_block_c{i:02d}.obj"
        p.export(os.path.join(out, fn))
        files.append(fn)
    block_visual.export(os.path.join(out, "knife_block_visual.obj"))
    pads.export(os.path.join(out, "pads_visual.obj"))
    pad_pieces = []
    for i, p in enumerate(pads.split(only_watertight=False)):
        fn = f"pads_c{i}.obj"
        p.convex_hull.export(os.path.join(out, fn))
        pad_pieces.append(fn)
    m, com, I = mass_props([(p, MATERIALS["beech_wood"]["density"]) for p in (mass_pieces or block_pieces)] +
                           [(p.convex_hull, MATERIALS["rubber"]["density"]) for p in pads.split(only_watertight=False)])
    if spec_in:  # the spec's own mass properties, as delivered
        o = spec_in["objects"]["knife_block"]
        m, com, I = o["mass_kg"], np.array(o["com_body_m"]), np.array(o["inertia_about_com_body_kgm2"])
    bodies.append({"name": "knife_block", "label": "Knife block", "pose": [0, 0, 0, 1, 0, 0, 0],
                   "inertial": {"mass": round(float(m), 5), "com": np.round(com, 6).tolist(), "fullinertia": fullinertia(I)},
                   "parts": [{"name": "body", "material": "beech_wood", "collision": files, "visual": "knife_block_visual.obj"},
                             {"name": "pads", "material": "rubber", "collision": pad_pieces, "visual": "pads_visual.obj"}]})
    for s in slots:
        name = s["name"]
        if own_knives:
            k = KNIVES[name]
            hl, hh, hw = k["handle"]
            blade = extrude(k["blade"], k["t"])
            bolster = trimesh.creation.box((BOLSTER, hh, hw), trimesh.transformations.translation_matrix((-BOLSTER / 2, hh / 2 - 0.002, 0)))
            handle = trimesh.creation.box((hl, hh * 0.92, hw * 0.92), trimesh.transformations.translation_matrix((-BOLSTER - hl / 2, hh / 2 - 0.002, 0)))
            parts = {"blade": (blade, "stainless_steel"), "bolster": (bolster, "stainless_steel"), "handle": (handle, "pom")}
            label = k["label"]
            # Seated: tip down the slot, blade normal across the slot (world x), spine on the slot's near side,
            # the bolster's front face resting on the block's top.
            R = np.c_[-AXIS, np.cross(EX, -AXIS), EX]
            pos = from_slot([s["x"], s["v0"] + 0.0006, wtop + 0.0003])
            quat = quat_from_matrix(R)
        else:
            parts, label, pos, quat = knife_meshes[name]
        mesh_parts = []
        for part, (mesh, mat) in parts.items():
            vis = f"{name}_{part}.obj"
            mesh.export(os.path.join(out, vis))
            col = f"{name}_{part}_c.obj"
            mesh.convex_hull.export(os.path.join(out, col))
            mesh_parts.append({"name": part, "material": mat, "collision": [col], "visual": vis})
        if spec_in:
            o = spec_in["objects"][name]
            km, kc, kI = o["mass_kg"], np.array(o["com_body_m"]), np.array(o["inertia_about_com_body_kgm2"])
        else:
            km, kc, kI = mass_props([(mesh, MATERIALS[mat]["density"]) for mesh, mat in parts.values()])
        # the whole knife as one hull, for the defect where its mesh was given to MuJoCo as one collision geom
        whole = trimesh.util.concatenate([mesh for mesh, _ in parts.values()]).convex_hull
        whole.export(os.path.join(out, f"{name}_hull.obj"))
        bodies.append({"name": name, "label": label, "slot": name, "pose": [*np.round(pos, 6).tolist(), *np.round(quat, 6).tolist()],
                       "inertial": {"mass": round(float(km), 5), "com": np.round(kc, 6).tolist(), "fullinertia": fullinertia(kI)},
                       "parts": mesh_parts, "hull": f"{name}_hull.obj"})
    slot_out = {s["name"]: {"x": s["x"], "v0": s["v0"], "width": s["width"], "length": s["length"], "depth": s["depth"],
                            "entry": np.round(from_slot([s["x"], s["v0"] + s["length"] / 2, wtop]), 5).tolist(),
                            "axis_out": np.round(AXIS, 6).tolist()} for s in slots}
    return {"kind": "set", "materials": MATERIALS, "bodies": bodies, "slots": slot_out, "collision": {},
            "frame": "z up; the block's footprint centre on the floor plane"}


def from_glb(glb, spec_path, out):
    """A set from its delivered meshes (a GLB, Y up) and its physical-properties spec (Z up)."""
    os.makedirs(out, exist_ok=True)
    spec = json.load(open(spec_path))
    scene = trimesh.load(glb, force="scene")
    yup = np.array([[-1, 0, 0, 0], [0, 0, 1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], float)  # GLB (x, y, z) -> (-x, z, y)
    world = {}
    for node in scene.graph.nodes_geometry:
        T, g = scene.graph[node]
        m = scene.geometry[g].copy()
        m.apply_transform(yup @ T)
        world[node] = m
    block = world["knife_block"]
    pads = world["pads"]
    wtop = float(np.mean([to_slot(np.array(s["entry"]))[2] for s in spec["slots"].values()]))
    slots = []
    for name, s in spec["slots"].items():
        e = to_slot(np.array(s["entry"]))
        slots.append({"name": name, "x": float(e[0]), "v0": float(e[1] - s["length"] / 2), "width": s["width"], "length": s["length"], "depth": s["depth"]})
    hs = hull_halfspaces(block.vertices)
    q = to_slot(block.vertices)
    pieces = wall_pieces(hs, slots, wtop, (q[:, 0].min(), q[:, 0].max()), (q[:, 1].min(), q[:, 1].max()))
    knife_meshes = {}
    mats = {"blade": "stainless_steel", "bolster": "stainless_steel", "handle": "pom"}
    for name, pose in spec["seated_poses"].items():
        if name == "knife_block":
            continue
        p, qw = np.array(pose["pos"]), np.array(pose["quat_wxyz"])
        Tk = trimesh.transformations.quaternion_matrix(qw)
        Tk[:3, 3] = p
        inv = np.linalg.inv(Tk)
        parts = {}
        for part in ("blade", "bolster", "handle"):
            m = world[f"{name}_{part}"].copy()
            m.apply_transform(inv)
            parts[part] = (m, mats[part])
        knife_meshes[name] = (parts, spec["objects"][name]["label"], p, qw)
    out_spec = assemble(out, pieces, block, pads, slots, wtop, own_knives=False, knife_meshes=knife_meshes, spec_in=spec)
    out_spec.update({"id": "knife_block_delivered", "label": "knife block",
                     "description": "A knife block with seven knives, from its delivered meshes and physical-properties spec.",
                     "credit": "Delivered meshes and spec; collision and MJCF by scripts/build_knife_block.py",
                     "designed_settle": {k: v.get("settled_from_geometric_seat") for k, v in spec["seated_poses"].items() if k != "knife_block"},
                     "hull_excess": {k: v.get("hull_excess") for k, v in spec["objects"].items() if "hull_excess" in v}})
    return out_spec


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("out")
    ap.add_argument("--procedural", action="store_true")
    ap.add_argument("--glb")
    ap.add_argument("--spec")
    a = ap.parse_args()
    spec = procedural(a.out) if a.procedural else from_glb(a.glb, a.spec, a.out)
    old = os.path.join(a.out, "asset.json")
    keep = json.load(open(old)) if os.path.isfile(old) else {}
    for k in ("task", "complaint", "checks", "collision"):
        if k in keep:
            spec[k] = keep[k]
    spec.setdefault("checks", [{"type": "set_seated", "mm": 2.0, "deg": 3.0}, {"type": "set_inserts", "mm": 2.0},
                               {"type": "set_pull_free", "max_weight_ratio": 2.0, "max_block_lift_mm": 2.0},
                               {"type": "set_tilt_min_deg", "value": 12}, {"type": "set_no_overlap", "end_mm": 0.5, "peak_mm": 3.0},
                               {"type": "set_mass_band", "lo": 2.0, "hi": 4.5}])
    json.dump(spec, open(old, "w"), indent=1)
    n = sum(len(p["collision"]) for b in spec["bodies"] for p in b["parts"])
    print(spec["id"], len(spec["bodies"]), "bodies,", n, "collision pieces, block pieces",
          len(spec["bodies"][0]["parts"][0]["collision"]), ", masses", {b["name"]: b["inertial"]["mass"] for b in spec["bodies"]})


if __name__ == "__main__":
    main()

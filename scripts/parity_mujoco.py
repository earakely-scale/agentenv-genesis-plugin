"""Run the workbench's probes on the same MJCF in Genesis and in MuJoCo, and compare.

The contributor page runs every action in Genesis on the server. A page that simulates in the browser would run MuJoCo
(its WebAssembly build) instead, with Genesis grading on the server. That works only if the two engines agree on what
a contributor sees. This script measures that: the tilt-table angle where each object first tips or slides, the drop
(settle time and final tilt), resting upright, and marbles staying in a cavity, for every mesh asset, as delivered and
with its edits, from one MJCF file each.

    python scripts/parity_mujoco.py --out docs/parity    # needs genesis-world, mujoco, trimesh, scipy; run from the repo
"""

import argparse
import json
import math
import os
import re
import sys
import time

import mujoco
import numpy as np

ENV = os.path.join(os.path.dirname(__file__), "..", "src", "agentenv_genesis", "bundles", "genesis-workbench", "envs",
                   "genesis-workbench")
sys.path.insert(0, ENV)
import assets as A  # noqa: E402

LEANS = list(range(10, 75, 5))
DT = 0.002
# The edits a contributor session converges on for each object, applied with the env's own tools.
FIXES = {
    "scan_mug": [("set_shell", "body", 0.004), ("set_material", "body", "ceramic")],
    "scan_mixing_bowl": [("set_shell", "body", 0.002), ("set_material", "body", "plastic_pp")],
    "cad_mug": [("set_scale", 1.45), ("set_inertial", 0.35, None, None)],
    "cad_serving_bowl": [("set_scale", 2.0), ("set_inertial", 1.9, None, None)],
    "cad_coffee_maker": [("set_shell", "housing", 0.003), ("set_shell", "steel", 0.0012), ("set_shell", "steel_2", 0.0012)],
    "cad_milk_frother": [("remove_part", "cord")],
}


def world(obj, z, free=True, extra=""):
    xml = obj.mjcf(free=free)
    xml = xml.replace('<body name="body">', f'<body name="body" pos="0 0 {z:.5f}">', 1)
    floor = '<geom name="floor" type="plane" size="3 3 0.1" friction="1 0.005 0.0001"/>'
    xml = xml.replace("<worldbody>", f"<worldbody>\n    {floor}{extra}", 1)
    return xml.replace(f'<mujoco model="{obj.name}">', f'<mujoco model="{obj.name}">\n  <option timestep="{DT}"/>', 1)


def tilt(q):
    w, x, y, z = q
    return math.degrees(math.acos(max(-1.0, min(1.0, 1 - 2 * (x * x + y * y)))))


def lowest(m, d):
    zs = []
    for g in range(m.ngeom):
        if m.geom_contype[g] == 0 or m.geom_type[g] != mujoco.mjtGeom.mjGEOM_MESH:
            continue
        k = m.geom_dataid[g]
        v = m.mesh_vert[m.mesh_vertadr[k]:m.mesh_vertadr[k] + m.mesh_vertnum[k]]
        zs.append(float((v @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g])[:, 2].min()))
    return min(zs)


def mj_tip(obj):
    tilts, slides = [], []
    for a in LEANS:
        m = mujoco.MjModel.from_xml_string(world(obj, obj._lift()))
        d = mujoco.MjData(m)
        mujoco.mj_forward(m, d)
        p0 = d.qpos[:2].copy()
        for k in range(900):
            f = min(1.0, k / 400)
            m.opt.gravity[:] = [9.81 * math.sin(math.radians(a) * f), 0.0, -9.81 * math.cos(math.radians(a) * f)]
            mujoco.mj_step(m, d)
        tilts.append(tilt(d.qpos[3:7]))
        slides.append(float(np.linalg.norm(d.qpos[:2] - p0)))
    mode = ["topple" if t > 30 else "slide" if s > 0.03 else "holds" for t, s in zip(tilts, slides)]
    first = next(((a, md) for a, md in zip(LEANS, mode) if md != "holds"), (None, "holds"))
    return {"first_failure_deg": first[0], "mode": first[1], "y": [round(t, 1) for t in tilts]}


def mj_drop(obj):
    b = obj._bounds()
    h = float(b[1][2] - b[0][2])
    m = mujoco.MjModel.from_xml_string(world(obj, obj._lift() + 2 * h))
    d = mujoco.MjData(m)
    settle, pen = None, 0.0
    for k in range(1200):
        mujoco.mj_step(m, d)
        if k % 25 == 0:
            pen = max(pen, -lowest(m, d))
            if settle is None and k > 50 and float(np.abs(d.qvel).max()) < 0.02:
                settle = round(k * DT, 2)
    return {"settles_s": settle, "final_tilt_deg": round(tilt(d.qpos[3:7]), 1), "max_penetration_mm": round(pen * 1000, 1)}


def mj_rest(obj):
    m = mujoco.MjModel.from_xml_string(world(obj, obj._lift()))
    d = mujoco.MjData(m)
    for _ in range(600):
        mujoco.mj_step(m, d)
    v, t = float(np.abs(d.qvel).max()), tilt(d.qpos[3:7])
    return {"max_speed": round(v, 4), "tilt_deg": round(t, 2), "stable": bool(v < 0.02 and t < 3)}


def mj_cavity(obj):
    b = obj._bounds()
    top = float(b[1][2] - b[0][2])
    cx, cy = [float(v) for v in (b[0][:2] + b[1][:2]) / 2]
    balls = "".join(f'\n    <body name="ball{i}" pos="{cx + 0.004 * (i - 2):.4f} {cy + 0.003 * (i % 2):.4f} {top + 0.03 + 0.025 * i:.4f}">'
                    f'<freejoint/><geom type="sphere" size="0.008" density="1000"/></body>' for i in range(5))
    m = mujoco.MjModel.from_xml_string(world(obj, obj._lift(), free=False, extra=balls))
    d = mujoco.MjData(m)
    for _ in range(1000):
        mujoco.mj_step(m, d)
    zs = [float(d.xpos[m.body(f"ball{i}").id][2]) for i in range(5)]
    inside = sum(1 for z in zs if z < top - 0.006)
    return {"balls_inside": inside, "open": inside >= 3}


def apply(obj, edits):
    for name, *args in edits:
        getattr(obj, name)(*args)


def compare(gen, mj):
    rows = []
    a, b = gen["tip"]["first_failure_deg"], mj["tip"]["first_failure_deg"]
    rows.append({"probe": "tip: first failure (deg)", "genesis": a, "mujoco": b, "genesis_mode": gen["tip"]["mode"],
                 "mujoco_mode": mj["tip"]["mode"], "agree": a == b or (a is not None and b is not None and abs(a - b) <= 5)})
    rows.append({"probe": "drop: final tilt (deg)", "genesis": gen["drop"]["final_tilt_deg"], "mujoco": mj["drop"]["final_tilt_deg"],
                 "agree": (gen["drop"]["final_tilt_deg"] < 10) == (mj["drop"]["final_tilt_deg"] < 10)})
    rows.append({"probe": "drop: settles (s)", "genesis": gen["drop"]["settles_s"], "mujoco": mj["drop"]["settles_s"],
                 "agree": (gen["drop"]["settles_s"] is None) == (mj["drop"]["settles_s"] is None)})
    rows.append({"probe": "rest: stable", "genesis": gen["rest"]["stable"], "mujoco": mj["rest"]["stable"],
                 "agree": gen["rest"]["stable"] == mj["rest"]["stable"]})
    if "cavity" in gen:
        rows.append({"probe": "cavity: marbles inside", "genesis": gen["cavity"]["balls_inside"], "mujoco": mj["cavity"]["balls_inside"],
                     "agree": gen["cavity"]["open"] == mj["cavity"]["open"]})
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="docs/parity")
    ap.add_argument("--objects", nargs="*", default=list(FIXES))
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    A.gs()
    results = []
    for name in a.objects:
        for variant in ("delivered", "edited"):
            obj = A.load(name)
            if variant == "edited":
                apply(obj, FIXES[name])
            container = any(c["type"] == "cavity_open" for c in obj.check_specs)
            t = time.time()
            gen = {k: obj.probe(k) for k in ["tip", "drop", "rest"] + (["cavity"] if container else [])}
            tg = time.time() - t
            t = time.time()
            mj = {"tip": mj_tip(obj), "drop": mj_drop(obj), "rest": mj_rest(obj)}
            if container:
                mj["cavity"] = mj_cavity(obj)
            tm = time.time() - t
            rows = compare(gen, mj)
            mass = obj.summary()["mass_kg"]
            results.append({"object": name, "variant": variant, "mass_kg": mass, "rows": rows,
                            "seconds": {"genesis": round(tg, 1), "mujoco": round(tm, 1)},
                            "tip_curve": {"x": LEANS, "genesis": gen["tip"]["y"], "mujoco": mj["tip"]["y"]}})
            agree = sum(r["agree"] for r in rows)
            print(f"{name:18s} {variant:9s} {mass:7.3f} kg  {agree}/{len(rows)} agree  "
                  + "  ".join(f"{r['probe'].split(':')[0]}={r['genesis']}|{r['mujoco']}" for r in rows), flush=True)
    json.dump(results, open(os.path.join(a.out, "parity.json"), "w"), indent=1, default=float)
    n = sum(len(r["rows"]) for r in results)
    k = sum(x["agree"] for r in results for x in r["rows"])
    print(f"agree on {k} of {n} probe readings")


if __name__ == "__main__":
    main()

"""Compose kitchen scenes in MuJoCo from objects the workbench approved and ReplicaCAD furniture converted to MJCF: the
kitchen counter, a fridge beside it, and the approved objects set out on the counter top, each layout from a seed. Each
scene is one folder with a scene.xml that attaches every object from its own MJCF file, which validate_mjcf.py then
checks: everything comes to rest, nothing is inside anything else, and every drawer and door opens all the way.

    python scripts/compose_kitchen.py --counter DIR --fridge DIR --objects EXPORT... --out DIR --seeds 0 1 2
"""

import argparse
import math
import re
import shutil
from pathlib import Path

import mujoco
import numpy as np

COUNTER_TOP = 0.96   # the converted counter: its top, its front edge (+x) and back, its ends (y)
COUNTER_X = (-0.47, 0.14)
COUNTER_Y = (-1.52, 1.47)


def object_files(folder: Path) -> list[tuple[str, str, list[float]]]:
    """An export's objects: (body name, MJCF file, pose in its own scene)."""
    scene = (folder / "scene.xml").read_text()
    out = []
    for mt in re.finditer(r'<frame pos="([^"]+)" quat="([^"]+)"><attach model="([^"]+)" body="([^"]+)"', scene):
        pos, quat, model, body = mt.groups()
        out.append((body, f"{model}.xml", [float(v) for v in pos.split()] + [float(v) for v in quat.split()]))
    return out


def footprint(folder: Path) -> tuple[float, float]:
    """An export's half extent on the floor (its whole scene) and its height, from MuJoCo."""
    m = mujoco.MjModel.from_xml_path(str(folder / "scene.xml"))
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    pts = []
    for g in range(m.ngeom):
        if m.geom_bodyid[g] == 0 or not (m.geom_contype[g] or m.geom_conaffinity[g]):
            continue
        k = m.geom_dataid[g]
        if k < 0:
            continue
        v = m.mesh_vert[m.mesh_vertadr[k]:m.mesh_vertadr[k] + m.mesh_vertnum[k]]
        pts.append(v @ d.geom_xmat[g].reshape(3, 3).T + d.geom_xpos[g])
    P = np.concatenate(pts)
    c = (P.max(0) + P.min(0)) / 2
    return float(np.linalg.norm(P[:, :2] - c[:2], axis=1).max()), float(P[:, 2].max())


def yaw_quat(a):
    return [math.cos(a / 2), 0.0, 0.0, math.sin(a / 2)]


def compose(counter: Path, fridge: Path, exports: list[Path], out: Path, seed: int, fridge_side: str) -> Path:
    rng = np.random.default_rng(seed)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copytree(counter, out / "counter")
    shutil.copytree(fridge, out / "fridge")
    models, frames = [], []
    cxml = next(p.name for p in counter.glob("*.xml"))
    fxml = next(p.name for p in fridge.glob("*.xml"))
    models += [f'<model name="counter" file="counter/{cxml}"/>', f'<model name="fridge" file="fridge/{fxml}"/>']
    frames.append('<frame pos="0 0 0.011"><attach model="counter" body="root" prefix="counter/"/></frame>')
    # the fridge stands beside the counter, its front flush with the counter's; on the right its doors hinge on the
    # counter's side, on the left on the far side
    fy = COUNTER_Y[1] + 0.105 + 0.358 if fridge_side == "right" else COUNTER_Y[0] - 0.055 - 0.358
    frames.append(f'<frame pos="-0.147 {fy:.4f} 0.962"><attach model="fridge" body="root" prefix="fridge/"/></frame>')
    # the approved objects, set out on the top at random, apart from each other
    placed = []
    for e in exports:
        r, _ = footprint(e)
        for _ in range(400):
            x = rng.uniform(COUNTER_X[0] + r + 0.02, COUNTER_X[1] - r - 0.02)
            y = rng.uniform(COUNTER_Y[0] + r + 0.02, COUNTER_Y[1] - r - 0.02)
            if all(math.hypot(x - px, y - py) > r + pr + 0.03 for px, py, pr in placed):
                break
        else:
            raise RuntimeError(f"no room on the counter for {e.name}")
        placed.append((x, y, r))
        a = rng.uniform(-math.pi, math.pi)
        shutil.copytree(e, out / e.name)
        R = np.array([[math.cos(a), -math.sin(a), 0], [math.sin(a), math.cos(a), 0], [0, 0, 1]])
        for body, f, pose in object_files(e):
            p = R @ np.asarray(pose[:3]) + [x, y, COUNTER_TOP + 0.002]
            q = np.zeros(4)
            mujoco.mju_mulQuat(q, np.array(yaw_quat(a)), np.asarray(pose[3:]))
            name = f"{e.name}_{body}"
            models.append(f'<model name="{name}" file="{e.name}/{f}"/>')
            frames.append(f'<frame pos="{p[0]:.5f} {p[1]:.5f} {p[2]:.5f}" quat="{q[0]:.6f} {q[1]:.6f} {q[2]:.6f} {q[3]:.6f}">'
                          f'<attach model="{name}" body="{body}" prefix="{name}/"/></frame>')
    doc = (f'<mujoco model="kitchen_{seed}">\n  <option timestep="0.001"/>\n  <asset>\n    ' + "\n    ".join(models) + "\n  </asset>\n"
           '  <worldbody>\n    <light pos="0 0 4" dir="0 0 -1" directional="true"/>\n'
           '    <geom name="floor" type="plane" size="6 6 0.1" friction="1 0.005 0.0001" solref="0.003 1"/>\n    '
           + "\n    ".join(frames) + "\n  </worldbody>\n</mujoco>\n")
    (out / "scene.xml").write_text(doc)
    mujoco.MjModel.from_xml_path(str(out / "scene.xml"))
    return out / "scene.xml"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--counter", required=True)
    ap.add_argument("--fridge", required=True)
    ap.add_argument("--objects", nargs="+", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2])
    a = ap.parse_args()
    for i, s in enumerate(a.seeds):
        side = "right" if i % 2 == 0 else "left"
        p = compose(Path(a.counter), Path(a.fridge), [Path(o) for o in a.objects], Path(a.out) / f"kitchen_{s}_fridge_{side}", s, side)
        m = mujoco.MjModel.from_xml_path(str(p))
        print(f"{p}: {m.nbody - 1} bodies, {m.njnt} joints, {sum(m.body_mass):.1f} kg")


if __name__ == "__main__":
    main()

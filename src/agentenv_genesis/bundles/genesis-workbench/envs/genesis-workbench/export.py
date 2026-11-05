"""Write an object the way it ships: an MJCF per object and a scene MJCF that places them, their OBJ meshes and textures,
the probe report, and a README. Every file is loaded back and stepped in plain MuJoCo before the export counts.

    <id>/scene.xml            the whole object or set on a floor, each object attached from its own file
    <id>/<object>.xml         one per object (a mesh object has one; a set has one per body)
    <id>/meshes/*.obj, *.png  collision pieces (group 3) and visual meshes (group 2)
    <id>/report.json          acceptance checks and probes in MuJoCo, Genesis's second opinion, the load check
    <id>/README.md
"""

from __future__ import annotations

import io
import json
import os
import re
import shutil
import zipfile

import mujoco
import numpy as np


def _object_docs(obj) -> dict[str, tuple[str, list[float]]]:
    """Each object's own MJCF (its body at the origin, meshes under meshes/) and where the scene places it."""
    if obj.kind == "mesh":
        doc = obj.mjcf(absolute=False).replace('file="./', 'file="').replace('<body name="body">', f'<body name="{obj.name}">', 1)
        head = re.match(r'<mujoco model="[^"]*">', doc).group(0)
        doc = doc.replace(head, head + '\n  <compiler meshdir="meshes" texturedir="meshes"/>', 1)
        return {obj.name: (doc, [0.0, 0.0, round(obj._lift(), 5), 1.0, 0.0, 0.0, 0.0])}
    if obj.kind == "set":
        out = {}
        for b in obj.spec["bodies"]:
            doc = obj.mjcf(bodies=[b["name"]], meshdir="meshes")
            doc = re.sub(r'<body name="([^"]+)" pos="[^"]*" quat="[^"]*">', r'<body name="\1">', doc)
            out[b["name"]] = (doc, b["pose"])
        return out
    raise ValueError(f"{obj.name} is a {obj.kind} object; only mesh objects and sets export as MJCF here")


def _scene(name: str, docs: dict) -> str:
    models = "\n    ".join(f'<model name="{n}" file="{n}.xml"/>' for n in docs)
    frames = "\n    ".join(
        f'<frame pos="{p[0]:.6f} {p[1]:.6f} {p[2]:.6f}" quat="{p[3]:.6f} {p[4]:.6f} {p[5]:.6f} {p[6]:.6f}">'
        f'<attach model="{n}" body="{n}" prefix="{n}/"/></frame>' for n, (_, p) in docs.items())
    return (f'<mujoco model="{name}_scene">\n  <option timestep="0.002"/>\n  <asset>\n    {models}\n  </asset>\n  <worldbody>\n'
            f'    <light pos="0 0 3" dir="0 0 -1" directional="true"/>\n'
            f'    <geom name="floor" type="plane" size="3 3 0.1" friction="1 0.005 0.0001"/>\n    {frames}\n  </worldbody>\n</mujoco>\n')


def validate(folder: str, names: list[str]) -> dict:
    """Load every MJCF in plain MuJoCo and step the scene for 1 s: each body's drift from where it starts."""
    out = {"mujoco": mujoco.__version__, "objects": {}}
    for n in names:
        m = mujoco.MjModel.from_xml_path(os.path.join(folder, f"{n}.xml"))
        out["objects"][n] = {"bodies": m.nbody - 1, "geoms": int(m.ngeom), "meshes": int(m.nmesh), "mass_kg": round(float(m.body_subtreemass[0]), 4)}
    m = mujoco.MjModel.from_xml_path(os.path.join(folder, "scene.xml"))
    d = mujoco.MjData(m)
    mujoco.mj_forward(m, d)
    p0 = d.xpos.copy()
    for _ in range(500):
        mujoco.mj_step(m, d)
    drift = {m.body(i).name: round(float(np.linalg.norm(d.xpos[i] - p0[i])) * 1000, 2) for i in range(1, m.nbody)}
    ok = bool(np.isfinite(d.qpos).all() and max(drift.values()) < 10)
    out["scene"] = {"bodies": m.nbody - 1, "drift_mm_after_1s": drift, "ok": ok}
    out["ok"] = ok
    return out


README = """# {label}: MJCF and OBJ for MuJoCo

{description}

- `scene.xml` loads everything, each object attached from its own file at its place: `mujoco.MjModel.from_xml_path("scene.xml")`.
- {objects}
- `meshes/`: the OBJ meshes and textures. Collision geoms are in group 3 and are convex pieces (MuJoCo collides every
  mesh as its convex hull); visual geoms are in group 2 and don't collide.
- Every body has an explicit `<inertial>` (mass, centre of mass, full inertia). Friction is set per geom from its
  part's material; `report.json` lists the materials, the acceptance checks and every probe.

Units are SI, z is up, quaternions are w x y z. Checked in MuJoCo {mujoco}: every file loads, and the scene settles
with no body drifting more than {drift:.1f} mm in 1 s.

Exported by the Genesis workbench (github.com/earakely-scale/agentenv-genesis-plugin) after its checks, which run in
MuJoCo with Genesis as a second opinion.
"""


def export(obj, folder: str, report: dict | None = None) -> dict:
    """Write `obj` to `folder` as MJCF and OBJ, check it loads, and return the load check."""
    if os.path.isdir(folder):
        shutil.rmtree(folder)
    os.makedirs(os.path.join(folder, "meshes"))
    docs = _object_docs(obj)
    for n, (doc, _) in docs.items():
        for f in set(re.findall(r'file="([^"]+)"', doc)):
            shutil.copy2(os.path.join(obj.dir, f), os.path.join(folder, "meshes", f))
        open(os.path.join(folder, f"{n}.xml"), "w").write(doc)
    open(os.path.join(folder, "scene.xml"), "w").write(_scene(obj.name, docs))
    check = validate(folder, list(docs))
    label = obj.spec["label"] if obj.kind == "set" else obj.model.get("label", obj.name)
    description = obj.spec["description"] if obj.kind == "set" else obj.model.get("description", "")
    objects = ("`" + "`, `".join(f"{n}.xml" for n in docs) + "`: each object alone, its body at the origin."
               if len(docs) > 1 else f"`{next(iter(docs))}.xml`: the object alone, its body at the origin.")
    open(os.path.join(folder, "README.md"), "w").write(README.format(
        label=label[:1].upper() + label[1:], description=description, objects=objects, mujoco=check["mujoco"],
        drift=max(check["scene"]["drift_mm_after_1s"].values())))
    json.dump({"object": obj.name, "kind": obj.kind, "load_check": check, **(report or {})},
              open(os.path.join(folder, "report.json"), "w"), indent=1, default=float)
    return check


def zipped(folder: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for root, _, files in os.walk(folder):
            for f in files:
                path = os.path.join(root, f)
                z.write(path, os.path.relpath(path, os.path.dirname(folder)))
    return buf.getvalue()

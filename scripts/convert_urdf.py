"""Convert a URDF (ReplicaCAD's, say) to MJCF for MuJoCo: its GLB meshes as OBJ, which MuJoCo can read, its visual
geoms kept (MuJoCo drops them from a URDF by default), and one MJCF file with its meshes beside it. Inertia, joints and
collision come across as the URDF gives them; scripts/validate_mjcf.py says what's wrong with them. The GLBs are kept
next to their OBJ so a viewer can show their textures.

    python scripts/convert_urdf.py URDF OUT_DIR          # OUT_DIR/<name>.xml and OUT_DIR/meshes/
    python scripts/convert_urdf.py URDF OUT_DIR --y-up   # authored for a Y-up world (Habitat's, as ReplicaCAD is): turn it Z-up
    python scripts/convert_urdf.py URDF OUT_DIR --solref "0.003 1"   # stiff contacts on every colliding geom, as the workbench writes
"""

import argparse
import math
import re
import shutil
import tempfile
from pathlib import Path

import mujoco
import numpy as np
import trimesh


def convert(urdf: Path, out: Path, y_up: bool = False, solref: str | None = None) -> tuple[Path, list[str]]:
    out.mkdir(parents=True, exist_ok=True)
    (out / "meshes").mkdir(exist_ok=True)
    text = urdf.read_text()
    for name in sorted(set(re.findall(r'filename="([^"]+)"', text))):
        src = urdf.parent / name
        stem = Path(name).stem
        if src.suffix.lower() in (".glb", ".gltf", ".dae", ".ply"):
            mesh = trimesh.load(src, force="mesh")  # the scene's nodes with their transforms, as one mesh
            mesh.export(out / "meshes" / f"{stem}.obj")
            shutil.copy2(src, out / "meshes" / src.name)
            text = text.replace(f'filename="{name}"', f'filename="{stem}.obj"')
        else:
            shutil.copy2(src, out / "meshes" / src.name)
            text = text.replace(f'filename="{name}"', f'filename="{src.name}"')
    # MuJoCo's own options for a URDF: keep the visual geoms, keep every link as a body, find meshes in meshes/
    text = re.sub(r"(<robot[^>]*>)", r'\1\n  <mujoco><compiler discardvisual="false" fusestatic="false" strippath="false" meshdir="meshes"/></mujoco>', text, 1)
    fixed = []

    def vector(mt):  # "0.617 0.145, 0.894" and "1.0, 1.0, 1.0" aren't URDF vectors; MuJoCo refuses them
        if "," in mt.group(2):
            fixed.append(f'{mt.group(1)}="{mt.group(2)}"')
        return f'{mt.group(1)}="{" ".join(mt.group(2).replace(",", " ").split())}"'
    text = re.sub(r'\b(size|xyz|rpy|scale|axis)="([^"]*)"', vector, text)
    tmp = Path(tempfile.mkdtemp(prefix="urdf-"))
    try:
        staged = out / f"{urdf.stem}.staged.urdf"
        staged.write_text(text)
        spec = mujoco.MjSpec.from_file(str(staged))
        if y_up:  # +90 deg about x takes +y to +z
            for b in spec.worldbody.bodies:
                q = np.zeros(4)
                mujoco.mju_mulQuat(q, np.array([math.cos(math.pi / 4), math.sin(math.pi / 4), 0, 0]), np.asarray(b.quat, dtype=float))
                b.quat = q
        if solref:
            for g in spec.geoms:
                if g.contype or g.conaffinity:
                    g.solref = [float(v) for v in solref.split()]
        spec.compile()
        xml = spec.to_xml()
        staged.unlink()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    name = re.sub(r"\W+", "_", urdf.stem.replace(".sanitized", ""))
    path = out / f"{name}.xml"
    path.write_text(xml)
    return path, fixed


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("urdf")
    ap.add_argument("out")
    ap.add_argument("--y-up", action="store_true")
    ap.add_argument("--solref")
    a = ap.parse_args()
    p, fixed = convert(Path(a.urdf), Path(a.out), a.y_up, a.solref)
    m = mujoco.MjModel.from_xml_path(str(p))
    vis = sum(1 for g in range(m.ngeom) if not (m.geom_contype[g] or m.geom_conaffinity[g]))
    print(f"{p}: {m.nbody - 1} bodies, {m.njnt} joints, {m.ngeom} geoms ({vis} visual), mass {sum(m.body_mass):.1f} kg"
          + (f"; fixed malformed vectors: {', '.join(fixed)}" if fixed else ""))


if __name__ == "__main__":
    main()

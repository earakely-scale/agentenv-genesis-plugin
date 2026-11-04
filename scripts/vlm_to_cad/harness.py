"""Fixed harness run inside the sandbox container: import a generated CadQuery script, export each part to STL.

Usage: python harness.py SCRIPT OUT_DIR
Writes OUT_DIR/parts/<i>_<material>.stl and OUT_DIR/result.json ({"ok", "error", "parts", "bbox_m"}).
Any exception, a missing or malformed `parts`, an unknown material or a part without solid geometry is an error,
reported with its traceback so the caller can ask the model for a repair.
"""

import json
import os
import runpy
import sys
import traceback

import cadquery as cq
import numpy as np
import trimesh

MATERIALS = {
    "ceramic", "porcelain", "glass", "stainless_steel", "cast_iron",
    "aluminum", "plastic_pp", "plastic_abs", "wood", "silicone",
}
STL_TOLERANCE_M = 1e-4
STL_ANGULAR_TOLERANCE = 0.1


def to_shape(obj) -> cq.Shape:
    if isinstance(obj, cq.Workplane):
        shapes = [v for v in obj.vals() if isinstance(v, cq.Shape)]
        if not shapes:
            raise ValueError("Workplane holds no shapes")
        return shapes[0] if len(shapes) == 1 else cq.Compound.makeCompound(shapes)
    if isinstance(obj, cq.Assembly):
        return obj.toCompound()
    if isinstance(obj, cq.Shape):
        return obj
    raise TypeError(f"part object is {type(obj).__name__}, not a cq.Workplane or cq.Shape")


def run(script: str, out: str) -> dict:
    ns = runpy.run_path(script, init_globals={"show_object": lambda *a, **k: None}, run_name="cad")
    if "parts" not in ns:
        raise NameError("the script does not define a module-level `parts` list")
    parts = ns["parts"]
    if not isinstance(parts, (list, tuple)) or not parts:
        raise ValueError("`parts` must be a non-empty list of (cq_object, material) tuples")
    os.makedirs(os.path.join(out, "parts"), exist_ok=True)
    listed, meshes = [], []
    for i, entry in enumerate(parts):
        if not isinstance(entry, (list, tuple)) or len(entry) != 2:
            raise ValueError(f"parts[{i}] is not a (cq_object, material) pair")
        obj, material = entry
        if material not in MATERIALS:
            raise ValueError(f"parts[{i}] material {material!r} is not one of {sorted(MATERIALS)}")
        shape = to_shape(obj)
        if not shape.Solids():
            raise ValueError(f"parts[{i}] ({material}) contains no solid geometry")
        rel = f"parts/{i}_{material}.stl"
        path = os.path.join(out, rel)
        cq.exporters.export(shape, path, tolerance=STL_TOLERANCE_M, angularTolerance=STL_ANGULAR_TOLERANCE)
        mesh = trimesh.load(path, force="mesh")
        if mesh.is_empty or len(mesh.faces) == 0:
            raise ValueError(f"parts[{i}] ({material}) exported an empty mesh")
        meshes.append(mesh)
        listed.append({
            "file": rel,
            "material": material,
            "volume_m3": float(shape.Volume()),
            "n_solids": len(shape.Solids()),
            "brep_valid": bool(shape.isValid()),
            "watertight": bool(mesh.is_watertight),
            "faces": int(len(mesh.faces)),
        })
    lo = np.min([m.bounds[0] for m in meshes], axis=0)
    hi = np.max([m.bounds[1] for m in meshes], axis=0)
    return {"ok": True, "error": None, "parts": listed, "bbox_m": (hi - lo).tolist(), "bounds_m": [lo.tolist(), hi.tolist()]}


def main() -> None:
    script, out = sys.argv[1], sys.argv[2]
    os.makedirs(out, exist_ok=True)
    try:
        result = run(script, out)
    except BaseException:
        result = {"ok": False, "error": traceback.format_exc(), "parts": [], "bbox_m": None}
    with open(os.path.join(out, "result.json"), "w") as f:
        json.dump(result, f, indent=2)
    print(json.dumps({"ok": result["ok"]}))


if __name__ == "__main__":
    main()

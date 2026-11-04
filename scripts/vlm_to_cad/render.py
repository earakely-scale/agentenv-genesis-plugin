"""Render two "photos" (3/4 and side, 512x512) of each GSO scan on a 1 cm grid with Genesis, offscreen.

Usage: python render.py MODELS_DIR OUT_DIR [Name ...]
Writes OUT_DIR/<Name>/view_34.png and view_side.png. The camera distance follows each mesh's bounding box,
so the photos alone carry no scale; the grid (1 cm cells, every fifth line darker) is the only ruler.
"""

import math
import os
import sys
import tempfile

import genesis as gs
import numpy as np
import trimesh
from PIL import Image

RES = 512
FOV = 40.0
GRID_PX_PER_CM = 32
VIEWS = {"34": (-50.0, 32.0), "side": (-90.0, 10.0)}


def grid_assets(side_m: float, workdir: str) -> str:
    """A square quad of `side_m` metres with a 1 cm grid texture, centred at the origin."""
    cells = int(round(side_m * 100))
    n = cells * GRID_PX_PER_CM
    img = np.full((n, n, 3), 236, np.uint8)
    for i in range(cells + 1):
        p = min(i * GRID_PX_PER_CM, n - 1)
        major = i % 5 == 0
        w = 4 if major else 2
        col = 70 if major else 150
        lo, hi = max(0, p - w // 2), min(n, p + (w + 1) // 2)
        img[lo:hi, :, :] = col
        img[:, lo:hi, :] = col
    tex = os.path.join(workdir, "grid.png")
    Image.fromarray(img).save(tex)
    h = side_m / 2
    obj = os.path.join(workdir, "grid.obj")
    with open(obj, "w") as f:
        f.write(f"v {-h} {-h} 0\nv {h} {-h} 0\nv {h} {h} 0\nv {-h} {h} 0\n")
        f.write("vt 0 0\nvt 1 0\nvt 1 1\nvt 0 1\nvn 0 0 1\n")
        f.write("f 1/1/1 2/2/1 3/3/1\nf 1/1/1 3/3/1 4/4/1\n")
    return obj


def render_one(model_dir: str, out_dir: str) -> None:
    mesh_path = os.path.join(model_dir, "model.obj")
    tex_path = os.path.join(model_dir, "texture.png")
    bounds = trimesh.load(mesh_path, force="mesh").bounds
    ext = bounds[1] - bounds[0]
    center = (bounds[0] + bounds[1]) / 2
    radius = 0.5 * float(np.linalg.norm(ext))
    dist = 1.15 * radius / math.sin(math.radians(FOV / 2))
    side = max(0.3, math.ceil(4.0 * max(ext) * 10) / 10)

    with tempfile.TemporaryDirectory() as work:
        grid_obj = grid_assets(side, work)
        scene = gs.Scene(
            show_viewer=False,
            vis_options=gs.options.VisOptions(
                ambient_light=(0.45, 0.45, 0.45),
                background_color=(0.85, 0.87, 0.9),
                shadow=True,
            ),
        )
        scene.add_entity(gs.morphs.Plane(visualization=False))
        scene.add_entity(
            gs.morphs.Mesh(file=grid_obj, pos=(0, 0, 0.0004), fixed=True, convexify=False, collision=False),
            surface=gs.surfaces.Default(diffuse_texture=gs.textures.ImageTexture(image_path=os.path.join(work, "grid.png"))),
        )
        scene.add_entity(
            gs.morphs.Mesh(file=mesh_path, fixed=True, convexify=False, collision=False),
            surface=gs.surfaces.Default(diffuse_texture=gs.textures.ImageTexture(image_path=tex_path)),
        )
        cams = {}
        for name, (az, el) in VIEWS.items():
            a, e = math.radians(az), math.radians(el)
            d = np.array([math.cos(e) * math.cos(a), math.cos(e) * math.sin(a), math.sin(e)])
            pos = center + dist * d
            cams[name] = scene.add_camera(res=(RES, RES), pos=tuple(pos), lookat=tuple(center), fov=FOV, GUI=False)
        scene.build()
        os.makedirs(out_dir, exist_ok=True)
        for name, cam in cams.items():
            rgb = cam.render(rgb=True)[0]
            Image.fromarray(np.asarray(rgb, dtype=np.uint8)).save(os.path.join(out_dir, f"view_{name}.png"))


def main() -> None:
    models, out = sys.argv[1], sys.argv[2]
    names = sys.argv[3:] or sorted(os.listdir(models))
    gs.init(backend=gs.cpu, logging_level="warning")
    for name in names:
        render_one(os.path.join(models, name), os.path.join(out, name))
        print("rendered", name, flush=True)


if __name__ == "__main__":
    main()

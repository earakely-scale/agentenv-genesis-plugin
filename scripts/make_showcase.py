"""Render hub/showcase.gif: each task's object as delivered beside the agent's fix, simulated and rendered by Genesis
from the MJCF recorded in the episodes.

    python scripts/make_showcase.py [--episodes hub/space/episodes.json] [--out hub/showcase.gif]
"""

import argparse
import json
import math
import os
import re
import tempfile

import numpy as np
from PIL import Image, ImageDraw, ImageFont

W, H, BAR = 960, 540, 118
FPS = 12
RED, TEAL = (0.86, 0.42, 0.36), (0.30, 0.74, 0.68)
INK, MUTED, ACC, PASS = (231, 236, 234), (143, 155, 151), (60, 194, 178), (76, 192, 114)
PICK = {"mug-tip": "mug-tip-sonnet-5-5-r1", "oven-door": "oven-door-sonnet-5-5-r1", "drawer-slide": "drawer-slide-sonnet-5-5-r1"}


def font(size, mono=False):
    for path in (["/System/Library/Fonts/Menlo.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"] if mono else
                 ["/System/Library/Fonts/Supplemental/Arial.ttf", "/System/Library/Fonts/Helvetica.ttc",
                  "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"]):
        if os.path.exists(path):
            return ImageFont.truetype(path, size)
    return ImageFont.load_default(size=size)


F_CAP, F_QUOTE, F_MONO, F_LABEL = font(13), font(25), font(14, mono=True), font(17)


def mug_visuals(xml: str) -> str:
    """Hide the collision cylinder and draw a hollow mug with a handle; visual geoms don't collide."""
    walls = "".join(
        f'<geom type="box" size="0.0019 0.0063 0.0475" pos="{0.0381 * math.cos(a):.4f} {0.0381 * math.sin(a):.4f} 0.0475" '
        f'euler="0 0 {math.degrees(a):.1f}" contype="0" conaffinity="0"/>' for a in [i * 2 * math.pi / 40 for i in range(40)])
    base = '<geom type="cylinder" size="0.038 0.003" pos="0 0 0.003" contype="0" conaffinity="0"/>'
    handle = ('<geom type="capsule" fromto="-0.040 0 0.075 -0.062 0 0.068" size="0.005" contype="0" conaffinity="0"/>'
              '<geom type="capsule" fromto="-0.062 0 0.068 -0.062 0 0.032" size="0.005" contype="0" conaffinity="0"/>'
              '<geom type="capsule" fromto="-0.062 0 0.032 -0.040 0 0.025" size="0.005" contype="0" conaffinity="0"/>')
    xml = re.sub(r'(<geom name="shell"[^>]*?)/>', r'\1 rgba="0 0 0 0"/>', xml)
    return xml.replace("</body>", walls + base + handle + "</body>", 1)


class Stage:
    def __init__(self, gs, xmls, offsets, cam_pos, lookat, fov, res, tmp, lips=()):
        self.scene = gs.Scene(sim_options=gs.options.SimOptions(dt=0.002), show_viewer=False,
                              vis_options=gs.options.VisOptions(show_world_frame=False))
        self.scene.add_entity(gs.morphs.Plane(), material=gs.materials.Rigid(friction=2.0))
        for x in lips:
            self.scene.add_entity(gs.morphs.Box(pos=(x, 0.0, 0.03), size=(0.012, 0.16, 0.06), fixed=True),
                                  material=gs.materials.Rigid(friction=2.0), surface=gs.surfaces.Default(color=(0.55, 0.6, 0.62)))
        self.ents = []
        for i, (xml, off, color) in enumerate(zip(xmls, offsets, (RED, TEAL))):
            path = os.path.join(tmp, f"obj{id(self)}_{i}.xml")
            with open(path, "w") as f:
                f.write(xml)
            self.ents.append(self.scene.add_entity(gs.morphs.MJCF(file=path, pos=off),
                                                   surface=gs.surfaces.Default(color=color)))
        self.cam = self.scene.add_camera(res=res, pos=cam_pos, lookat=lookat, fov=fov, GUI=False)
        self.scene.build()

    def frame(self):
        return Image.fromarray(np.asarray(self.cam.render(rgb=True)[0]))


def overlay(img, n, ep, edits, labels, result=None):
    canvas = Image.new("RGB", (W, H), (14, 18, 19))
    canvas.paste(img.resize((W, H - BAR)), (0, BAR))
    d = ImageDraw.Draw(canvas)
    d.text((24, 16), f"GENESISWORKBENCHENV · AGENTENV × GENESIS   {n}/3 · {ep['object']}", font=F_CAP, fill=MUTED)
    d.text((24, 38), f"“{ep['complaint']}”", font=F_QUOTE, fill=INK)
    d.text((24, 80), fit(d, list(edits), W - 48), font=F_MONO, fill=ACC)
    for (x, text, rgb) in labels:
        tw = d.textlength(text, font=F_LABEL)
        d.rounded_rectangle((x - tw / 2 - 10, H - 46, x + tw / 2 + 10, H - 16), radius=8, fill=(14, 18, 19))
        d.text((x - tw / 2, H - 42), text, font=F_LABEL, fill=rgb)
    if result:
        tw = d.textlength(result, font=F_LABEL)
        d.rounded_rectangle((W - tw - 44, BAR + 14, W - 18, BAR + 46), radius=8, fill=(20, 48, 31))
        d.text((W - tw - 32, BAR + 19), result, font=F_LABEL, fill=PASS)
    return canvas


def edit_calls(ep):
    out = []
    for c in ep["calls"]:
        if c["tool"].startswith("set_"):
            args = ", ".join(f'{k}={compact(v)}' for k, v in c["args"].items())
            out.append(f"{c['tool']}({args})")
    return out[-2:] if len(out) > 2 else out


def compact(v):
    if isinstance(v, float):
        return f"{v:g}"
    if isinstance(v, list):
        return "[" + ",".join(compact(x) for x in v) + "]"
    return json.dumps(v)


def fit(d, parts, width):
    line = "agent: " + "  ·  ".join(parts)
    while parts and d.textlength(line, font=F_MONO) > width:
        parts = parts[:-1]
        line = "agent: " + "  ·  ".join(parts)
    return line


def result_text(ep):
    ok = sum(c["result"] for c in ep["checks"])
    return f"hidden probes {ok}/{len(ep['checks'])} · score {ep['score']:.2f} · {ep['model']}"


def mug_segment(gs, ep, tmp):
    import torch
    stage = Stage(gs, [mug_visuals(ep["before"]["mjcf"]), mug_visuals(ep["after"]["mjcf"])], [(-0.11, 0, 0), (0.11, 0, 0)],
                  (0.0, -1.25, 0.08), (0.0, 0.0, 0.05), 30, (1280, 820), tmp, lips=(0.255,))
    frames, edits, lean = [], edit_calls(ep), 50.0
    steps, toppled_at, a = int(2.6 / 0.002), None, 0.0
    for k in range(steps):
        if toppled_at is None:
            a = lean * min(1.0, k / 700)
            w, x, y, z = stage.ents[1].get_quat().cpu().numpy().ravel()
            if math.degrees(math.acos(max(-1, min(1, 1 - 2 * (x * x + y * y))))) > 60:
                toppled_at, a_top = k, a
        else:
            a = max(0.0, a_top * (1 - (k - toppled_at) / 250))
        stage.scene.rigid_solver.set_gravity(torch.tensor([9.81 * math.sin(math.radians(a)), 0.0, -9.81 * math.cos(math.radians(a))]))
        stage.scene.step()
        if k % int(1 / (FPS * 0.002)) == 0:
            img = stage.frame().rotate(-a, resample=Image.BICUBIC, fillcolor=(14, 18, 19))
            cw, ch = img.size
            img = img.crop(((cw - 900) // 2, (ch - 506) // 2, (cw + 900) // 2, (ch + 506) // 2))
            tilts = []
            for e in stage.ents:
                w, x, y, z = e.get_quat().cpu().numpy().ravel()
                tilts.append(math.degrees(math.acos(max(-1, min(1, 1 - 2 * (x * x + y * y))))))
            state = ["toppled" if t > 60 else "upright" for t in tilts]
            labels = [(W * 0.34, f"as delivered · {state[0]}", (235, 140, 125)),
                      (W * 0.66, f"after the agent's fix · {state[1]}", (110, 215, 200))]
            done = k > steps * 0.7
            frames.append(overlay(img, 1, ep, [f"tilt table {a:.0f}°"] + edits, labels, result_text(ep) if done else None))
    return frames


def door_segment(gs, ep, tmp):
    stage = Stage(gs, [ep["before"]["mjcf"], ep["after"]["mjcf"]], [(-0.55, 0, 0), (0.55, 0, 0)],
                  (0.75, -2.6, 1.15), (0.0, -0.15, 0.3), 34, (1280, 820), tmp)
    for e in stage.ents:
        dof = [e.get_joint("door_hinge").dofs_idx_local[0]]
        e.set_dofs_position(np.array([math.radians(5)]), dofs_idx_local=dof, zero_velocity=True)
    frames, edits = [], edit_calls(ep)
    steps = int(2.4 / 0.002)
    for k in range(steps):
        stage.scene.step()
        if k % int(1 / (FPS * 0.002)) == 0:
            ang = [math.degrees(float(e.get_dofs_position([e.get_joint("door_hinge").dofs_idx_local[0]]).cpu().numpy().ravel()[0]))
                   for e in stage.ents]
            labels = [(W * 0.30, f"as delivered · door {ang[0]:.0f}°", (235, 140, 125)),
                      (W * 0.70, f"after the agent's fix · door {ang[1]:.0f}°", (110, 215, 200))]
            frames.append(overlay(stage.frame(), 2, ep, ["unlatched at 5°"] + edits, labels,
                                  result_text(ep) if k > steps * 0.6 else None))
    return frames


def drawer_segment(gs, ep, tmp):
    stage = Stage(gs, [ep["before"]["mjcf"], ep["after"]["mjcf"]], [(-0.48, 0, 0), (0.48, 0, 0)],
                  (0.8, -2.7, 1.6), (0.0, -0.2, 0.5), 32, (1280, 820), tmp)
    links = [e.get_link("drawer") for e in stage.ents]
    y0 = [float(l.get_pos().cpu().numpy().ravel()[1]) for l in links]
    frames, edits = [], edit_calls(ep)
    steps = int(2.0 / 0.002)
    for k in range(steps):
        if k < 300:
            for l in links:
                l.apply_external_force(np.array([0.0, -10.0, 0.0]))
        stage.scene.step()
        if k % int(1 / (FPS * 0.002)) == 0:
            out = [y - float(l.get_pos().cpu().numpy().ravel()[1]) for y, l in zip(y0, links)]
            labels = [(W * 0.32, f"as delivered · out {out[0] * 100:.0f} cm", (235, 140, 125)),
                      (W * 0.68, f"after the agent's fix · out {out[1] * 100:.0f} cm", (110, 215, 200))]
            frames.append(overlay(stage.frame(), 3, ep, ["10 N pull"] + edits, labels,
                                  result_text(ep) if k > steps * 0.55 else None))
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", default="hub/space/episodes.json")
    ap.add_argument("--out", default="hub/showcase.gif")
    a = ap.parse_args()
    eps = {e["id"]: e for e in json.load(open(a.episodes))}
    import genesis as gs
    gs.init(backend=gs.cpu, seed=0, logging_level="warning")
    tmp = tempfile.mkdtemp(prefix="showcase-")
    frames = []
    for seg, task in ((mug_segment, "mug-tip"), (door_segment, "oven-door"), (drawer_segment, "drawer-slide")):
        f = seg(gs, eps[PICK[task]], tmp)
        frames += f + [f[-1]] * 14
        print(task, len(f), "frames")
    pal = [f.convert("P", palette=Image.ADAPTIVE, colors=192) for f in frames]
    pal[0].save(a.out, save_all=True, append_images=pal[1:], duration=int(1000 / FPS), loop=0, optimize=True, disposal=1)
    print(a.out, f"{os.path.getsize(a.out) / 1e6:.1f} MB", len(frames), "frames")


if __name__ == "__main__":
    main()

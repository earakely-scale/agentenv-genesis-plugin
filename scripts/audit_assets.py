"""Audit sim-ready assets with plausibility probes in Genesis: what breaks when real meshes go in as delivered.

Sources: Google Scanned Objects as MJCF (rigid kitchen objects), ReplicaCAD URDFs (articulated furniture), and the
VLM-to-CAD outputs from scripts/vlm_to_cad (parts plus materials). Writes results JSON and evidence renders.

    python scripts/audit_assets.py --gso DIR --replicacad DIR [--vlmcad DIR] --out DIR
"""

import argparse
import json
import math
import os
import re
import time

import numpy as np
import trimesh

DENSITY = {"ceramic": 2300, "porcelain": 2400, "glass": 2500, "stainless_steel": 7900, "cast_iron": 7200,
           "aluminum": 2700, "plastic_pp": 905, "plastic_abs": 1050, "wood": 700, "silicone": 1100, "plant_fiber": 1300}

# Category, material and an approximate real weight band (kg, our estimate from typical retail specs) per scanned object.
GSO = {
    "ACE_Coffee_Mug_Kristen_16_oz_cup": ("mug", "ceramic", (0.30, 0.45), True),
    "Cole_Hardware_Mug_Classic_Blue": ("mug", "ceramic", (0.25, 0.40), True),
    "BIA_Porcelain_Ramekin_With_Glazed_Rim_35_45_oz_cup": ("ramekin", "porcelain", (0.08, 0.16), True),
    "Bradshaw_International_11642_7_Qt_MP_Plastic_Bowl": ("mixing bowl", "plastic_pp", (0.25, 0.55), True),
    # Named a 12" cast-iron fry pan in GSO, but the scan is a white footed serving bowl, 32 cm across.
    "Calphalon_Kitchen_Essentials_12_Cast_Iron_Fry_Pan_Black": ("bowl (mislabelled pan)", "ceramic", None, True),
    "Chefmate_8_Frypan": ("frying pan", "aluminum", (0.4, 0.8), True),
    "Chef_Style_Round_Cake_Pan_9_inch_pan": ("cake pan", "aluminum", (0.2, 0.45), True),
    "Aroma_Stainless_Steel_Milk_Frother_2_Cup": ("milk frother", "stainless_steel", (0.4, 1.2), False),
    "Black_Decker_CM2035B_12Cup_Thermal_Coffeemaker": ("coffee maker", "plastic_abs", (2.2, 3.6), False),
    "Footed_Bowl_Sand": ("bowl", "ceramic", (0.3, 0.8), True),
    "Cole_Hardware_Deep_Bowl_Good_Earth_1075": ("bowl", "ceramic", (0.8, 2.5), True),
    "Ecoforms_Plate_S20Avocado": ("plate", "plant_fiber", (0.1, 0.35), False),
}


class G:
    gs = None

    @classmethod
    def init(cls):
        if cls.gs is None:
            import genesis as gs
            gs.init(backend=gs.cpu, seed=0, logging_level="warning")
            cls.gs = gs
        return cls.gs


def scene(n_envs=1, friction=1.0, dt=0.002, cam=None, self_collision=True):
    gs = G.init()
    s = gs.Scene(sim_options=gs.options.SimOptions(dt=dt), show_viewer=False,
                 rigid_options=gs.options.RigidOptions(enable_self_collision=self_collision),
                 vis_options=gs.options.VisOptions(show_world_frame=False))
    s.add_entity(gs.morphs.Plane(), material=gs.materials.Rigid(friction=friction))
    return s


def tilt_of(ent):
    w, x, y, z = ent.get_quat().cpu().numpy().reshape(-1, 4).T
    return np.degrees(np.arccos(np.clip(1 - 2 * (x * x + y * y), -1, 1)))


def with_freejoint(xml_path: str) -> str:
    """GSO bodies have no joint, so they're welded to the world; add a free joint for the dynamic probes."""
    src = open(xml_path).read()
    out = os.path.join(os.path.dirname(xml_path), "model_free.xml")
    open(out, "w").write(re.sub(r'(<body name="model"[^>]*>)', r'\1<freejoint/>', src, count=1))
    return out


def mesh_stats(obj_path):
    m = trimesh.load(obj_path, force="mesh")
    ext = m.bounds[1] - m.bounds[0]
    return m, {"watertight": bool(m.is_watertight), "bbox_m": [round(float(v), 4) for v in ext],
               "volume_cm3": round(float(abs(m.volume)) * 1e6, 1) if m.is_watertight else None,
               "hull_volume_cm3": round(float(m.convex_hull.volume) * 1e6, 1)}


def rigid_probes(path, label, container, evidence_png=None):
    """Probes for one free rigid object loaded from MJCF."""
    gs = G.init()
    out = {}
    # static: mass, COM, AABB, collision geoms
    s = scene()
    e = s.add_entity(gs.morphs.MJCF(file=path))
    s.build()
    aabb = e.get_AABB().cpu().numpy().reshape(2, 3)
    com = e.get_links_COM().cpu().numpy().reshape(-1, 3)[0]
    out.update({"mass_kg": round(float(e.get_mass()), 4), "n_collision_geoms": int(e.n_geoms),
                "com_height_cm": round(float(com[2] - aabb[0][2]) * 100, 2), "aabb_m": [round(float(v), 4) for v in aabb[1] - aabb[0]]})
    lift = -float(aabb[0][2])
    # rest + drop
    s = scene()
    e = s.add_entity(gs.morphs.MJCF(file=path, pos=(0, 0, lift + 2 * float(aabb[1][2] - aabb[0][2]))))
    s.build()
    settle, vmax_end, pen = None, None, 0.0
    for k in range(1500):
        s.step()
        if k % 25 == 0:
            v = float(np.abs(e.get_dofs_velocity().cpu().numpy()).max())
            z0 = float(e.get_AABB().cpu().numpy().reshape(2, 3)[0][2])
            pen = max(pen, -z0)
            if settle is None and k > 50 and v < 0.01:
                settle = round(k * 0.002, 2)
            vmax_end = v
    out["drop"] = {"settles_s": settle, "max_penetration_mm": round(pen * 1000, 1), "final_speed": round(vmax_end, 4),
                   "final_tilt_deg": round(float(tilt_of(e)[0]), 1)}
    # tilt table: per-env gravity, ramp then hold; record topple or slide
    leans = list(range(10, 75, 5))
    s = scene(n_envs=len(leans))
    e = s.add_entity(gs.morphs.MJCF(file=path, pos=(0, 0, lift + 0.001)))
    s.build(n_envs=len(leans))
    import torch
    p0 = e.get_pos().cpu().numpy().reshape(-1, 3).copy()
    for k in range(900):
        f = min(1.0, k / 400)
        g = [[9.81 * math.sin(math.radians(a) * f), 0.0, -9.81 * math.cos(math.radians(a) * f)] for a in leans]
        s.rigid_solver.set_gravity(torch.tensor(g, dtype=torch.float32))
        s.step()
    tilt = tilt_of(e)
    slide = np.linalg.norm((e.get_pos().cpu().numpy().reshape(-1, 3) - p0)[:, :2], axis=1)
    mode = ["topple" if t > 30 else "slide" if d > 0.03 else "holds" for t, d in zip(tilt, slide)]
    first = next(((a, m) for a, m in zip(leans, mode) if m != "holds"), (None, "holds"))
    out["tilt_table"] = {"first_failure_deg": first[0], "mode": first[1], "leans": leans, "modes": mode}
    # cavity: drop small spheres into the opening of a container, object welded in place
    if container:
        s = scene()
        e = s.add_entity(gs.morphs.MJCF(file=path.replace("model_free.xml", "model.xml") if path.endswith("model_free.xml") else path,
                                        pos=(0, 0, lift)))
        top = float(aabb[1][2] - aabb[0][2])
        balls = [s.add_entity(gs.morphs.Sphere(radius=0.008, pos=(0.004 * (i - 2), 0.003 * (i % 2), top + 0.03 + 0.025 * i)),
                              material=gs.materials.Rigid(rho=1000)) for i in range(5)]
        cam = s.add_camera(res=(480, 360), pos=(0.0, -max(0.25, 2.2 * top), top * 1.9 + 0.05), lookat=(0, 0, top * 0.45), fov=40,
                           GUI=False) if evidence_png else None
        s.build()
        for _ in range(1000):
            s.step()
        zs = [float(b.get_pos().cpu().numpy().ravel()[2]) for b in balls]
        inside = sum(1 for z in zs if z < top - 0.006)
        out["cavity"] = {"rim_height_cm": round(top * 100, 1), "ball_heights_cm": [round(z * 100, 1) for z in zs],
                         "balls_inside": inside, "open": inside >= 3}
        if cam:
            from PIL import Image
            Image.fromarray(np.asarray(cam.render(rgb=True)[0])).save(evidence_png)
    return out


def audit_gso(root, out_dir):
    rows = []
    for name, (label, material, band, container) in GSO.items():
        d = os.path.join(root, name)
        if not os.path.isdir(d):
            continue
        t0 = time.time()
        _, ms = mesh_stats(os.path.join(d, "model.obj"))
        free = with_freejoint(os.path.join(d, "model.xml"))
        r = {"source": "gso", "object": name, "label": label, "material": material, "real_mass_band_kg": band, "mesh": ms}
        try:
            r["as_provided"] = rigid_probes(free, label, container, os.path.join(out_dir, f"cavity_{name}.png") if container else None)
        except Exception as ex:  # noqa: BLE001
            r["error"] = repr(ex)[:400]
        rho = DENSITY[material]
        vol = ms["volume_cm3"] or ms["hull_volume_cm3"]
        r["solid_at_material_density_kg"] = round(vol * 1e-6 * rho, 3)
        r["seconds"] = round(time.time() - t0, 1)
        rows.append(r)
        print(name, json.dumps({k: r.get(k) for k in ("solid_at_material_density_kg",)}), r.get("as_provided", {}).get("mass_kg"), r.get("error"))
    return rows


def audit_replicacad(root, out_dir):
    gs = G.init()
    rows = []
    for rel in ("kitchen_cupboards/kitchenCupboard_01.urdf", "chest_of_drawers/chestOfDrawers_01.urdf",
                "kitchen_counter/kitchen_counter.urdf", "fridge/fridge.urdf", "cabinet/cabinet.urdf"):
        path = os.path.join(root, "urdf", rel)
        src = open(path).read()
        comma_scales = len(re.findall(r'scale="[^"]*,[^"]*"', src))
        comma_sizes = len(re.findall(r'size="[^"]*,[^"]*"', src))
        dummy_inertia = len(re.findall(r'ixx="1\.0"[^/]*iyy="1\.0"[^/]*izz="1\.0"', src))
        fixed_path = path.replace(".urdf", ".sanitized.urdf")
        clean = re.sub(r'(scale|size)="([^"]*)"', lambda m: '%s="%s"' % (m.group(1), " ".join(m.group(2).replace(",", " ").split())), src)
        open(fixed_path, "w").write(clean)
        for mode in ("as_delivered", "no_self_collision"):
            r = {"source": "replicacad", "object": rel, "mode": mode, "comma_separated_scales": comma_scales,
                 "malformed_box_sizes": comma_sizes,
                 "links_with_placeholder_inertia": dummy_inertia,
                 "collision_types": sorted(set(re.findall(r"<collision[^>]*>\s*<origin[^>]*/>\s*<geometry>\s*<(\w+)", src)))}
            t0 = time.time()
            try:
                s = scene(self_collision=(mode == "as_delivered"))
                e = s.add_entity(gs.morphs.URDF(file=fixed_path, fixed=True))
                s.build()
                links = [{"name": l.name, "n_geoms": l.n_geoms, "mass_kg": round(float(l.get_mass()), 2)} for l in e.links]
                joints = []
                for j in e.joints:
                    kind = {1: "revolute", 2: "prismatic"}.get(int(j.type))
                    if kind is None:
                        continue
                    dof = int(j.dofs_idx_local[0])
                    lim = e.get_dofs_limit([dof])
                    lo, hi = float(np.asarray(lim[0].cpu()).ravel()[0]), float(np.asarray(lim[1].cpu()).ravel()[0])
                    joints.append({"name": j.name, "type": kind, "lower": round(lo, 3), "upper": round(hi, 3), "dof": dof})
                reach = []
                for j in joints:
                    target = j["upper"] if abs(j["upper"]) >= abs(j["lower"]) else j["lower"]
                    dof = [j["dof"]]
                    e.set_dofs_position(np.zeros(1), dofs_idx_local=dof, zero_velocity=True)
                    e.set_dofs_kp(np.array([400.0]), dofs_idx_local=dof)
                    e.set_dofs_kv(np.array([40.0]), dofs_idx_local=dof)
                    e.control_dofs_position(np.array([target * 0.9]), dofs_idx_local=dof)
                    for _ in range(600):
                        s.step()
                    got = float(e.get_dofs_position(dof).cpu().numpy().ravel()[0])
                    reach.append({"joint": j["name"], "target": round(target * 0.9, 3), "reached": round(got, 3),
                                  "fraction": round(got / (target * 0.9), 2) if target else None})
                    e.control_dofs_position(np.zeros(1), dofs_idx_local=dof)
                    for _ in range(300):
                        s.step()
                r.update({"links": links, "joints": joints, "reach": reach,
                          "unstable": bool(s.rigid_solver.get_error_envs_mask().cpu().numpy().any())
                          if hasattr(s.rigid_solver, "get_error_envs_mask") else None})
            except Exception as ex:  # noqa: BLE001
                r["error"] = repr(ex)[:400]
            r["seconds"] = round(time.time() - t0, 1)
            rows.append(r)
            print(rel, mode, r.get("error") or [(x["joint"], x["fraction"]) for x in r.get("reach", [])][:8])
    return rows


def vlmcad_mjcf(d, manifest):
    """Assemble one free body from the VLM's parts: mass properties from each closed part at its material's density."""
    parts, total_m, com_acc, inertia = [], 0.0, np.zeros(3), np.zeros((3, 3))
    meshes = []
    for i, p in enumerate(manifest["parts"]):
        m = trimesh.load(os.path.join(d, p["file"]), force="mesh")
        rho = DENSITY.get(p["material"], 1000)
        vol = abs(m.volume) if abs(m.volume) > 0 else m.convex_hull.volume  # CAD solids: near-closed STLs keep their volume
        mass = vol * rho
        meshes.append((m, mass, p))
        total_m += mass
        com_acc += mass * (m.center_mass if abs(m.volume) > 0 else m.convex_hull.center_mass)
    com = com_acc / max(total_m, 1e-9)
    for m, mass, p in meshes:
        mm = (m if abs(m.volume) > 0 else m.convex_hull).copy()
        mm.density = mass / max(abs(mm.volume), 1e-12)
        dv = mm.center_mass - com
        inertia += mm.moment_inertia + mass * (np.dot(dv, dv) * np.eye(3) - np.outer(dv, dv))
    ev = np.clip(np.linalg.eigvalsh(inertia), 1e-8, None)
    assets = "".join(f'<mesh name="p{i}" file="{os.path.join(d, p["file"])}"/>' for i, (_, _, p) in enumerate(meshes))
    geoms = "".join(f'<geom type="mesh" mesh="p{i}"/>' for i in range(len(meshes)))
    xml = (f'<mujoco model="{manifest["object"]}"><asset>{assets}</asset><worldbody><body name="model"><freejoint/>'
           f'<inertial pos="{com[0]:.5f} {com[1]:.5f} {com[2]:.5f}" mass="{total_m:.4f}" diaginertia="{ev[0]:.3e} {ev[1]:.3e} {ev[2]:.3e}"/>'
           f'{geoms}</body></worldbody></mujoco>')
    path = os.path.join(d, "assembled.xml")
    open(path, "w").write(xml)
    return path, total_m, com


def audit_vlmcad(root, out_dir):
    rows = []
    for name in sorted(os.listdir(root)):
        mf = os.path.join(root, name, "manifest.json")
        if not os.path.isfile(mf):
            continue
        man = json.load(open(mf))
        label, material, band, container = GSO.get(name, ("?", "?", None, False))
        r = {"source": "vlmcad", "object": name, "label": man.get("label"), "ok": man.get("ok"), "real_mass_band_kg": band,
             "materials": sorted({p["material"] for p in man.get("parts", [])}), "bbox_m": man.get("bbox_m")}
        if man.get("ok"):
            try:
                path, mass, com = vlmcad_mjcf(os.path.join(root, name), man)
                r["assembled_mass_kg"] = round(mass, 4)
                r["probes"] = rigid_probes(path, label, container, os.path.join(out_dir, f"cavity_vlm_{name}.png") if container else None)
            except Exception as ex:  # noqa: BLE001
                r["error"] = repr(ex)[:400]
        rows.append(r)
        print("vlmcad", name, r.get("assembled_mass_kg"), r.get("error"))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--gso")
    ap.add_argument("--replicacad")
    ap.add_argument("--vlmcad")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    for key, fn in (("gso", audit_gso), ("replicacad", audit_replicacad), ("vlmcad", audit_vlmcad)):
        src = getattr(a, key)
        if src:
            rows = fn(src, a.out)
            json.dump(rows, open(os.path.join(a.out, f"{key}.json"), "w"), indent=1)


if __name__ == "__main__":
    main()

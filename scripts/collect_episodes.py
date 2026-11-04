"""Turn the episodes the verifier keeps (GENESIS_EPISODES_DIR) into results/episodes/*.json and results/renders/** for
scripts/publish_hub.py.

    python scripts/collect_episodes.py --raw raw/r1-sonnet=claude-sonnet-5-5 --raw raw/r2-opus=claude-opus-5-5
"""

import argparse
import base64
import json
import math
import re
from pathlib import Path

TASKS = {"mug_017": "mug-tip", "oven_004": "oven-door", "cabinet_022": "drawer-slide"}
ASSETS = Path(__file__).resolve().parent.parent / "src/agentenv_genesis/bundles/genesis-workbench/envs/genesis-workbench/assets"
SPECS = {d.name: json.loads((d / "asset.json").read_text()) for d in ASSETS.iterdir() if (d / "asset.json").is_file()} if ASSETS.is_dir() else {}
TASKS.update({k: v["task"] for k, v in SPECS.items()})
# Scale-task runs recorded before source_photo existed; the published set keeps only runs of the current env.
SUPERSEDED = {("r4", "cad_mug"), ("r4", "cad_serving_bowl"), ("r5", "cad_mug"), ("r5", "cad_serving_bowl")}


def target_text(spec):
    out = []
    for c in spec["checks"]:
        t = c["type"]
        out.append({"mass_band": f"mass {c.get('lo')}–{c.get('hi')} kg", "height_band": f"height {c.get('lo', 0) * 100:g}–{c.get('hi', 0) * 100:g} cm",
                    "width_band": f"width {c.get('lo', 0) * 100:g}–{c.get('hi', 0) * 100:g} cm", "cavity_open": "the opening holds dropped objects",
                    "rests_upright": "rests upright", "tilt_min_deg": f"stays put on a tilt table to {c.get('value')}°",
                    "no_floating_parts": "no floating parts", "joints_reach": "every door reaches 90% of its travel"}[t])
    return "; ".join(out)


def extra_metrics(text, checks):
    out = {}
    m = re.search(r'mass="([0-9.]+)"', text or "")
    if m:
        out["mass_kg"] = float(m.group(1))
    m = re.search(r'"scale": ([0-9.]+)', text or "")
    if m:
        out["scale"] = float(m.group(1))
    m = re.search(r'"self_collision": (true|false)', text or "")
    if m:
        out["self_collision"] = m.group(1) == "true"
    for c in checks:
        g = re.search(r"got (.+)$", c["description"])
        if g:
            out[f"check.{c['id']}"] = g.group(1)
    return out
TARGETS = {"tip": "topples on a tilt table near a real mug's 45.4°",
           "hinge": "released at 5°, opens to at least 85° within 2 s",
           "travel": "a 10 N pull opens it at least 0.30 m, and it stops before leaving the cabinet"}
REAL_MUG_TIP_DEG = math.degrees(math.atan(0.040 / 0.0395))


def scalars(probes: dict, links: dict | None = None) -> dict:
    out = {}
    for kind, p in probes.items():
        for k, v in p.items():
            if isinstance(v, (int, float, bool, str)) and k not in ("kind", "note", "x_label", "y_label") or v is None:
                out[f"{kind}.{k}"] = v
    return out


def convert(raw: dict, model: str, tag: str, results: Path) -> dict:
    task = TASKS[raw["object"]]
    eid = f"{task}-{model.removeprefix('claude-')}-{tag}"
    renders = results / "renders" / eid
    renders.mkdir(parents=True, exist_ok=True)
    calls = []
    for c in raw["calls"]:
        image = None
        if c.get("image_png_b64"):
            path = renders / f"{c['i']:02d}-{c['tool']}.png"
            path.write_bytes(base64.b64decode(c["image_png_b64"]))
            image = f"renders/{eid}/{path.name}"
        calls.append({"i": c["i"], "t": c.get("t"), "tool": c["tool"],
                      "args": {k: v for k, v in c["args"].items() if v is not None}, "result": c["result"], "image": image})
    p = dict(raw["probe"])
    bp, ap = raw["before"].get("probes", {}), raw["after"].get("probes", {})
    if not p.get("x"):
        for k in ("tip", "joints", "hinge", "travel", "drop"):
            if k in ap and ap[k].get("x") and k in bp:
                p = {"kind": k, "x": ap[k]["x"], "x_label": ap[k]["x_label"], "y_label": ap[k]["y_label"],
                     "before": bp[k].get("y"), "after": ap[k].get("y")}
                break
        else:
            if "parts" in ap and "parts" in bp:
                names = list(bp["parts"]["gap_to_nearest_part_mm"])
                p = {"kind": "parts", "x": names, "x_label": "part", "y_label": "gap to nearest part (mm)",
                     "before": [bp["parts"]["gap_to_nearest_part_mm"].get(n) for n in names],
                     "after": [ap["parts"]["gap_to_nearest_part_mm"].get(n) for n in names]}
    if p.get("x") and any(isinstance(v, str) for v in p["x"]):
        names = p["x"]
        p["x"] = list(range(1, len(names) + 1))
        p["x_label"] = "joint " + ", ".join(f"{i}={n}" for i, n in enumerate(names, 1))
    reference = ([90.0 if a > REAL_MUG_TIP_DEG else 0.0 for a in p["x"]]
                 if p["kind"] == "tip" and raw["object"] == "mug_017" else None)
    return {"id": eid, "task": task, "object": raw["object"], "complaint": raw["complaint"], "model": model,
            "harness": "claude-code", "score": raw["score"], "passed": raw["passed"], "duration_s": raw["duration_s"],
            "checks": [{"id": c["id"], "result": bool(c["result"]), "description": c["description"]} for c in raw["checks"]],
            "calls": calls,
            "before": {"mjcf": raw["before"]["mjcf"], "metrics": {**scalars(raw["before"]["probes"]),
                                                                 **extra_metrics(raw["before"]["mjcf"], raw["before"]["checks"])}},
            "after": {"mjcf": raw["after"]["mjcf"], "metrics": {**scalars(raw["after"]["probes"]),
                                                               **extra_metrics(raw["after"]["mjcf"], raw["checks"])}},
            "diff": raw["diff"],
            "probe": {"kind": p["kind"], "x": p["x"], "x_label": p["x_label"], "y_label": p["y_label"],
                      "before": p["before"], "after": p["after"], "reference": reference,
                      "target": TARGETS.get(p["kind"]) if raw["object"] in ("mug_017", "oven_004", "cabinet_022") else target_text(SPECS[raw["object"]])}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", action="append", required=True, help="DIR=MODEL, a folder of raw episodes and the model that ran them")
    ap.add_argument("--results", default="results")
    a = ap.parse_args()
    results = Path(a.results)
    (results / "episodes").mkdir(parents=True, exist_ok=True)
    for spec in a.raw:
        folder, model = spec.split("=", 1)
        tag = Path(folder).name.removeprefix("episodes-").split("-")[0]
        for f in sorted(Path(folder).glob("*.json")):
            raw = json.loads(f.read_text())
            if raw.get("complaint") in (None, "", "wiring") or not raw.get("calls"):
                print(f"skip {f.name}: no agent calls")
                continue
            if (tag, raw.get("object")) in SUPERSEDED:
                print(f"skip {f.name}: superseded by a run with source_photo")
                continue
            ep = convert(raw, model, tag, results)
            (results / "episodes" / f"{ep['id']}.json").write_text(json.dumps(ep, indent=1))
            print(f"{ep['id']}: score {ep['score']}, {len(ep['calls'])} calls")


if __name__ == "__main__":
    main()

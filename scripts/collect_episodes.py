"""Turn the episodes the verifier keeps (GENESIS_EPISODES_DIR) into results/episodes/*.json and results/renders/** for
scripts/publish_hub.py.

    python scripts/collect_episodes.py --raw raw/r1-sonnet=claude-sonnet-5-5 --raw raw/r2-opus=claude-opus-5-5
"""

import argparse
import base64
import json
import math
from pathlib import Path

TASKS = {"mug_017": "mug-tip", "oven_004": "oven-door", "cabinet_022": "drawer-slide"}
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
    p = raw["probe"]
    reference = [90.0 if a > REAL_MUG_TIP_DEG else 0.0 for a in p["x"]] if p["kind"] == "tip" else None
    return {"id": eid, "task": task, "object": raw["object"], "complaint": raw["complaint"], "model": model,
            "harness": "claude-code", "score": raw["score"], "passed": raw["passed"], "duration_s": raw["duration_s"],
            "checks": [{"id": c["id"], "result": bool(c["result"]), "description": c["description"]} for c in raw["checks"]],
            "calls": calls,
            "before": {"mjcf": raw["before"]["mjcf"], "metrics": scalars(raw["before"]["probes"])},
            "after": {"mjcf": raw["after"]["mjcf"], "metrics": scalars(raw["after"]["probes"])},
            "diff": raw["diff"],
            "probe": {"kind": p["kind"], "x": p["x"], "x_label": p["x_label"], "y_label": p["y_label"],
                      "before": p["before"], "after": p["after"], "reference": reference, "target": TARGETS[p["kind"]]}}


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
            ep = convert(raw, model, tag, results)
            (results / "episodes" / f"{ep['id']}.json").write_text(json.dumps(ep, indent=1))
            print(f"{ep['id']}: score {ep['score']}, {len(ep['calls'])} calls")


if __name__ == "__main__":
    main()

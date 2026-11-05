"""Validate a set the way the workbench grades it, for the Space's report: every probe in MuJoCo with its trace, the
hidden checks in MuJoCo and Genesis, the static tipping margin, and the MJCF and OBJ export loaded back in MuJoCo. Run it
on the object as delivered and with the edits that fix it.

    python scripts/build_validation_report.py knife_block --fix set_collision chef_20cm parts --fix set_collision bread_20cm parts
    python scripts/build_validation_report.py NAME --asset-dir DIR --out DIR     # a set from anywhere, as delivered only
"""

import argparse
import base64
import json
import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / "src/agentenv_genesis/bundles/genesis-workbench/envs/genesis-workbench"
sys.path.insert(0, str(ENV))
import assets as A  # noqa: E402
import export as X  # noqa: E402


def run(obj, label: str, export_to: Path | None) -> dict:
    probes = {}
    for kind in ("rest", "seat", "extract", "tip"):
        obj._trace = {"frames": [], "t": []}
        r = obj.probe(kind)
        tr = A.node_frames(obj._trace, obj.visual_nodes())
        obj._trace = None
        probes[kind] = {"result": r, "trace": {**tr, "kind": kind}}
    rows, _, _ = obj.checks("current")
    g_rows, _, _ = obj.checks("current", "genesis")
    graded = {r["id"]: r["result"] for r in rows}
    out = {"label": label, "edits": json.loads(obj.text()), "checks": rows, "passed": all(r["result"] for r in rows),
           "second_opinion": {"engine": "genesis", "checks": g_rows, "rest": obj.probe("rest", "genesis"),
                              "disagree": [r["id"] for r in g_rows if r["id"] in graded and graded[r["id"]] != r["result"]]},
           "stability": obj.stability(), "probes": probes}
    if export_to is not None:
        folder = export_to / obj.name
        out["load_check"] = X.export(obj, str(folder), {k: out[k] for k in ("checks", "second_opinion", "stability")})
        (export_to / f"{obj.name}.zip").write_bytes(X.zipped(str(folder)))
        shutil.rmtree(folder)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("name")
    ap.add_argument("--asset-dir")
    ap.add_argument("--fix", nargs=3, action="append", default=[], metavar=("TOOL", "ARG1", "ARG2"))
    ap.add_argument("--out", default=str(ROOT / "hub" / "space"))
    a = ap.parse_args()
    out = Path(a.out)
    (out / "validation").mkdir(parents=True, exist_ok=True)
    (out / "scenes").mkdir(parents=True, exist_ok=True)
    exports = out / "exports"
    exports.mkdir(parents=True, exist_ok=True)
    spec = json.load(open(Path(a.asset_dir) / "asset.json")) if a.asset_dir else None
    obj = A.SetObject(a.name, spec) if spec else A.load(a.name)
    if a.asset_dir:
        obj.dir = a.asset_dir
    A.gs()
    report = {"object": a.name, "label": obj.spec["label"], "description": obj.spec["description"], "complaint": obj.spec.get("complaint"),
              "bodies": [{"name": b["name"], "label": b.get("label"), "mass_kg": b["inertial"]["mass"]} for b in obj.spec["bodies"]],
              "slots": obj.spec["slots"], "states": []}
    glb, meta = A.web_scene(obj, original=True)
    (out / "scenes" / f"{a.name}.glb").write_bytes(glb)
    (out / "scenes" / f"{a.name}.json").write_text(json.dumps(meta))
    report["states"].append(run(obj, "as delivered", None if a.fix else exports))
    if a.fix:
        for tool, x, y in a.fix:
            getattr(obj, tool)(x, y)
        report["states"].append(run(obj, "after the fix", exports))
    report["export"] = f"exports/{a.name}.zip"
    (out / "validation" / f"{a.name}.json").write_text(json.dumps(report, separators=(",", ":"), default=float))
    for s in report["states"]:
        print(s["label"], "passed" if s["passed"] else "failed", {r["id"]: r["result"] for r in s["checks"]},
              "| genesis disagrees on", s["second_opinion"]["disagree"], "| tips back at", s["stability"]["tip_angle_deg"]["back"])
    print("report", (out / "validation" / f"{a.name}.json").stat().st_size // 1024, "KB; export", report["export"])


if __name__ == "__main__":
    main()

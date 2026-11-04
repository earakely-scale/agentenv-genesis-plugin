"""Photos -> VLM -> CadQuery -> STL parts with materials, the generated code run only in a sandbox container.

Usage: python run.py OUT_DIR [--log-dir DIR] [--workers N] [Name ...]
OUT_DIR holds the renders (OUT_DIR/<Name>/view_*.png, from render.py). For each object this asks the model once,
runs the code in a `--network none` container on the sandbox host through harness.py, and on a failure sends the
error back for one repair. It writes OUT_DIR/<Name>/{cad.py, parts/, manifest.json} and OUT_DIR/summary.json.

Environment: ANTHROPIC_BASE_URL and ANTHROPIC_API_KEY for the model endpoint; VLMCAD_SSH, the command that runs a
shell command on the sandbox host (it is given the command as its last argument), which needs Docker and the
image `vlmcad-runner:cq280` (python:3.12-slim with cadquery==2.8.0, trimesh, numpy).
"""

import argparse
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
import urllib.error
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import trimesh

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import call  # noqa: E402

IMAGE = "vlmcad-runner:cq280"
TIMEOUT_S = 120
MAX_ATTEMPTS = 2
PRICE_IN, PRICE_OUT = 3.0, 15.0

# Step 1's output: the category a detector would give each object, never its product name.
LABELS = {
    "ACE_Coffee_Mug_Kristen_16_oz_cup": "mug",
    "Aroma_Stainless_Steel_Milk_Frother_2_Cup": "milk frother",
    "BIA_Porcelain_Ramekin_With_Glazed_Rim_35_45_oz_cup": "ramekin",
    "Black_Decker_CM2035B_12Cup_Thermal_Coffeemaker": "coffee maker",
    "Bradshaw_International_11642_7_Qt_MP_Plastic_Bowl": "mixing bowl",
    "Calphalon_Kitchen_Essentials_12_Cast_Iron_Fry_Pan_Black": "bowl",
    "Chef_Style_Round_Cake_Pan_9_inch_pan": "cake pan",
    "Chefmate_8_Frypan": "frying pan",
    "Cole_Hardware_Deep_Bowl_Good_Earth_1075": "bowl",
    "Cole_Hardware_Mug_Classic_Blue": "mug",
    "Ecoforms_Plate_S20Avocado": "plate",
    "Footed_Bowl_Sand": "bowl",
}


def ssh_cmd() -> list[str]:
    return shlex.split(os.environ["VLMCAD_SSH"])


def upload_harness() -> None:
    with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "harness.py"), "rb") as f:
        subprocess.run(ssh_cmd() + ['mkdir -p "$HOME/vlmcad" && cat > "$HOME/vlmcad/harness.py"'], input=f.read(), check=True)
    out = subprocess.run(ssh_cmd() + [f"docker image inspect -f ok {IMAGE}"], capture_output=True, text=True)
    if out.stdout.strip() != "ok":
        raise SystemExit(f"image {IMAGE} is missing on the sandbox host")


def run_remote(name: str, attempt: int, code: str) -> dict:
    """Run one generated script on the sandbox host; returns the harness's result.json or an error result."""
    cname = f"vlmcad-{abs(hash(name)) % 10**8}-a{attempt}"
    d = f'"$HOME/vlmcad/jobs/{name}/a{attempt}"'
    script = f"""set -u
rm -rf {d}; mkdir -p {d}/out; cat > {d}/cad.py
timeout -k 5 {TIMEOUT_S} docker run --rm --name {cname} --network none --memory 4g --cpus 2 --pids-limit 512 \\
  --cap-drop ALL --security-opt no-new-privileges \\
  -v {d}:/work -v "$HOME/vlmcad/harness.py":/opt/harness.py:ro {IMAGE} \\
  python /opt/harness.py /work/cad.py /work/out > {d}/log.txt 2>&1
echo $? > {d}/exit
docker rm -f {cname} >/dev/null 2>&1 || true
tar -C {d} -czf - out log.txt exit
"""
    proc = subprocess.run(ssh_cmd() + [script], input=code.encode(), capture_output=True, timeout=TIMEOUT_S + 120)
    work = tempfile.mkdtemp(prefix="vlmcad-")
    try:
        with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:gz") as tar:
            tar.extractall(work, filter="data")
    except tarfile.TarError:
        return {"ok": False, "error": f"sandbox run failed: {proc.stderr.decode()[-2000:]}", "_work": work}
    exit_code = open(os.path.join(work, "exit")).read().strip()
    log = open(os.path.join(work, "log.txt"), errors="replace").read()
    res_path = os.path.join(work, "out", "result.json")
    if exit_code == "124":
        res = {"ok": False, "error": f"The script did not finish within {TIMEOUT_S} s and was killed."}
    elif exit_code == "137":
        res = {"ok": False, "error": "The script was killed (out of memory: the limit is 4 GB)."}
    elif os.path.exists(res_path):
        res = json.load(open(res_path))
    else:
        res = {"ok": False, "error": f"The harness produced no result (exit {exit_code}):\n{log[-3000:]}"}
    res["_work"], res["_log"], res["_exit"] = work, log, exit_code
    return res


def complete_with_retry(messages: list[dict]) -> tuple[str, dict]:
    for i in range(3):
        try:
            return call.complete(messages)
        except (urllib.error.URLError, TimeoutError) as e:
            status = getattr(e, "code", None)
            if i == 2 or (status is not None and status < 500 and status != 429):
                raise
            time.sleep(5 * (i + 1))
    raise AssertionError("unreachable")


def union_bbox(paths: list[str]) -> list[float]:
    meshes = [trimesh.load(p, force="mesh") for p in paths]
    lo = np.min([m.bounds[0] for m in meshes], axis=0)
    hi = np.max([m.bounds[1] for m in meshes], axis=0)
    return [round(float(v), 6) for v in hi - lo]


def process(name: str, out_root: str, log_root: str) -> dict:
    obj_dir, log_dir = os.path.join(out_root, name), os.path.join(log_root, name)
    os.makedirs(log_dir, exist_ok=True)
    label = LABELS[name]
    images = sorted(os.path.join(obj_dir, f) for f in os.listdir(obj_dir) if f.startswith("view_") and f.endswith(".png"))
    messages = call.first_messages(label, images)
    usage = {"input_tokens": 0, "output_tokens": 0}
    attempts, error, res = 0, None, None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            text, u = complete_with_retry(messages)
        except Exception as e:  # the endpoint failed; record it and stop
            error = f"model call failed: {e!r}"
            break
        attempts = attempt
        usage = {k: usage[k] + u[k] for k in usage}
        code = call.extract_code(text)
        with open(os.path.join(obj_dir, "cad.py"), "w") as f:
            f.write(code)
        with open(os.path.join(log_dir, f"attempt{attempt}_cad.py"), "w") as f:
            f.write(code)
        res = run_remote(name, attempt, code)
        with open(os.path.join(log_dir, f"attempt{attempt}_result.json"), "w") as f:
            json.dump({k: v for k, v in res.items() if k != "_work"}, f, indent=2)
        if res.get("ok"):
            error = None
            break
        error = res.get("error") or "unknown error"
        print(f"[{name}] attempt {attempt} failed: {error.strip().splitlines()[-1][:200]}", flush=True)
        messages = call.repair_messages(messages, code, error)

    parts_dir = os.path.join(obj_dir, "parts")
    shutil.rmtree(parts_dir, ignore_errors=True)
    parts, bbox = [], None
    if res and res.get("ok"):
        os.makedirs(parts_dir)
        for p in res["parts"]:
            shutil.copy(os.path.join(res["_work"], "out", p["file"]), os.path.join(obj_dir, p["file"]))
            parts.append({"file": p["file"], "material": p["material"]})
        bbox = union_bbox([os.path.join(obj_dir, p["file"]) for p in parts])
    if res and res.get("_work"):
        shutil.rmtree(res["_work"], ignore_errors=True)
    manifest = {
        "object": name,
        "label": label,
        "ok": bool(res and res.get("ok")),
        "attempts": attempts,
        "error": error,
        "parts": parts,
        "bbox_m": bbox,
        "model": call.MODEL,
        "usage": usage,
    }
    with open(os.path.join(obj_dir, "manifest.json"), "w") as f:
        json.dump(manifest, f, indent=2)
    print(f"[{name}] ok={manifest['ok']} attempts={attempts} bbox={bbox} usage={usage}", flush=True)
    return manifest


def write_summary(out_root: str, models_dir: str | None) -> dict:
    objects, tin, tout = [], 0, 0
    for name in sorted(os.listdir(out_root)):
        mpath = os.path.join(out_root, name, "manifest.json")
        if not os.path.exists(mpath):
            continue
        m = json.load(open(mpath))
        row = {k: m[k] for k in ("object", "label", "ok", "attempts", "bbox_m", "usage")}
        if models_dir and os.path.exists(os.path.join(models_dir, name, "model.obj")):
            b = trimesh.load(os.path.join(models_dir, name, "model.obj"), force="mesh").bounds
            row["scan_bbox_m"] = [round(float(v), 6) for v in b[1] - b[0]]
        objects.append(row)
        tin += m["usage"]["input_tokens"]
        tout += m["usage"]["output_tokens"]
    summary = {
        "model": call.MODEL,
        "objects": objects,
        "n_ok": sum(o["ok"] for o in objects),
        "n_objects": len(objects),
        "total_usage": {"input_tokens": tin, "output_tokens": tout},
        "estimated_cost_usd": round(tin / 1e6 * PRICE_IN + tout / 1e6 * PRICE_OUT, 4),
        "pricing_usd_per_mtok": {"input": PRICE_IN, "output": PRICE_OUT},
    }
    with open(os.path.join(out_root, "summary.json"), "w") as f:
        json.dump(summary, f, indent=2)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("out_dir")
    ap.add_argument("names", nargs="*")
    ap.add_argument("--log-dir", default=None)
    ap.add_argument("--models-dir", default=None, help="the scans, only to record their real bbox in summary.json")
    ap.add_argument("--workers", type=int, default=4)
    args = ap.parse_args()
    names = args.names or sorted(LABELS)
    log_root = args.log_dir or tempfile.mkdtemp(prefix="vlmcad-logs-")
    upload_harness()
    with ThreadPoolExecutor(args.workers) as pool:
        list(pool.map(lambda n: process(n, args.out_dir, log_root), names))
    s = write_summary(args.out_dir, args.models_dir)
    print(json.dumps({k: s[k] for k in ("n_ok", "n_objects", "total_usage", "estimated_cost_usd")}))


if __name__ == "__main__":
    main()

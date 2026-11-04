#!/usr/bin/env python3
"""Publish GenesisWorkbenchEnv episodes to the Hugging Face Space viewer and dataset.

Reads results/episodes/*.json and results/renders/**, writes hub/space/episodes.json (with the renders beside
it) and hub/dataset/episodes.jsonl, then creates both repos if missing and uploads them. Every file goes
through a leak guard first; a file that matches is left out of the upload and reported.
"""
from __future__ import annotations

import argparse
import json
import shutil
import re
import struct
import sys
import tempfile
import zlib
from pathlib import Path

try:
    from huggingface_hub import HfApi
except ImportError:
    HfApi = None

ROOT = Path(__file__).resolve().parent.parent
HUB = ROOT / "hub"
SPACE_DIR = HUB / "space"
DATASET_DIR = HUB / "dataset"
DEFAULT_REPO = "earakely-scale/GenesisWorkbenchEnv-AgentEnv"
TASK_ORDER = ("mug-tip", "oven-door", "drawer-slide")
REQUIRED = ("id", "task", "object", "complaint", "model", "harness", "score", "passed", "duration_s",
            "checks", "calls", "before", "after", "diff", "probe")
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif"}
LEAK_PATTERNS = (rb"sk-[a-z0-9_\-]{12,}", rb"litellm-proxy", rb"scale\.com")
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def png_text(data: bytes) -> bytes:
    """A PNG's text and metadata chunks, decompressed: compressed pixel data matches short strings by chance."""
    if not data.startswith(PNG_SIGNATURE):
        return data
    out, pos = [], len(PNG_SIGNATURE)
    try:
        while pos + 8 <= len(data):
            length, kind = struct.unpack(">I4s", data[pos:pos + 8])
            body = data[pos + 8:pos + 8 + length]
            pos += 12 + length
            if kind == b"tEXt" or kind == b"eXIf":
                out.append(body)
            elif kind == b"zTXt":
                key, _, rest = body.partition(b"\0")
                out += [key, zlib.decompress(rest[1:])]
            elif kind == b"iTXt":
                key, _, rest = body.partition(b"\0")
                compressed, rest = rest[0], rest[2:]
                lang, _, rest = rest.partition(b"\0")
                translated, _, text = rest.partition(b"\0")
                out += [key, lang, translated, zlib.decompress(text) if compressed else text]
            elif kind == b"IEND":
                break
    except (struct.error, zlib.error, IndexError):
        return data
    return b"\n".join(out)


def leak_hits(path: Path) -> list[str]:
    data = path.read_bytes()
    if path.suffix.lower() == ".png":
        data = png_text(data)
    low = data.lower()
    return [p.decode() for p in LEAK_PATTERNS if re.search(p, low)]


def load_episodes(results: Path) -> list[dict]:
    files = sorted((results / "episodes").glob("*.json"))
    episodes = []
    for f in files:
        try:
            doc = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            print(f"skip {f}: not readable JSON ({exc})")
            continue
        for ep in doc if isinstance(doc, list) else [doc]:
            missing = [k for k in REQUIRED if not isinstance(ep, dict) or k not in ep]
            if missing:
                print(f"skip an episode in {f}: missing {', '.join(missing)}")
                continue
            episodes.append(ep)
    seen, unique = set(), []
    for ep in episodes:
        if ep["id"] in seen:
            print(f"skip a second episode with id {ep['id']}")
            continue
        seen.add(ep["id"])
        unique.append(ep)
    order = {t: i for i, t in enumerate(TASK_ORDER)}
    return sorted(unique, key=lambda e: (order.get(e["task"], len(order)), str(e["id"])))


def image_path(image, results: Path) -> str | None:
    """The call's frame as a path under renders/, the way the Space and the dataset both serve it."""
    if not image:
        return None
    renders = results / "renders"
    p = Path(str(image))
    if p.is_absolute():
        try:
            return "renders/" + p.resolve().relative_to(renders.resolve()).as_posix()
        except ValueError:
            print(f"warn: frame {image} is outside {renders}; the viewer will show it as missing")
            return "renders/" + p.name
    parts = [x for x in p.parts if x not in (".", "")]
    if parts[:1] == [results.name]:
        parts = parts[1:]
    if parts[:1] == ["renders"]:
        parts = parts[1:]
    rel = Path(*parts) if parts else Path(p.name)
    if not (renders / rel).is_file():
        print(f"warn: frame renders/{rel.as_posix()} not found; the viewer will show it as missing")
    return "renders/" + rel.as_posix()


def dataset_row(ep: dict) -> dict:
    """calls[].args and the metrics vary in keys and types by task, so they go to the dataset as JSON strings."""
    row = json.loads(json.dumps(ep))
    for call in row.get("calls", []):
        call["args"] = json.dumps(call.get("args", {}), sort_keys=True, ensure_ascii=False)
    for side in ("before", "after"):
        if isinstance(row.get(side), dict):
            row[side]["metrics"] = json.dumps(row[side].get("metrics", {}), sort_keys=True, ensure_ascii=False)
    return row


def copy_renders(results: Path, dest: Path) -> int:
    if dest.exists():
        shutil.rmtree(dest)
    src = results / "renders"
    n = 0
    if src.is_dir():
        for f in sorted(src.rglob("*")):
            if f.is_file() and f.suffix.lower() in IMAGE_SUFFIXES:
                target = dest / f.relative_to(src)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(f, target)
                n += 1
    return n


def stage(files: dict[str, Path], staging: Path) -> tuple[list[str], list[tuple[str, list[str]]]]:
    """Copy each file that passes the leak guard into staging; return what was kept and what was refused."""
    kept, refused = [], []
    for rel, src in sorted(files.items()):
        hits = leak_hits(src)
        if hits:
            refused.append((rel, hits))
            continue
        target = staging / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, target)
        kept.append(rel)
    return kept, refused


def tree(base: Path, sub: str) -> dict[str, Path]:
    d = base / sub
    return {f.relative_to(base).as_posix(): f for f in sorted(d.rglob("*")) if f.is_file()} if d.is_dir() else {}


def publish(api, label: str, repo_id: str, repo_type: str, files: dict[str, Path], data_file: str,
            dry_run: bool, n_episodes: int) -> bool:
    with tempfile.TemporaryDirectory(prefix=f"publish-{repo_type}-") as tmp:
        staging = Path(tmp)
        kept, refused = stage(files, staging)
        size = sum((staging / k).stat().st_size for k in kept)
        print(f"\n{label} {repo_type} {repo_id}: {len(kept)} file(s), {size / 1024:.0f} KiB")
        for rel in kept:
            if not rel.startswith("renders/"):
                print(f"  upload {rel}")
        renders = sum(r.startswith("renders/") for r in kept)
        if renders:
            print(f"  upload renders/: {renders} frame(s)")
        for rel, hits in refused:
            print(f"  SKIPPED {rel}: contains {', '.join(repr(h) for h in hits)}")
        if any(rel == data_file for rel, _ in refused):
            print(f"  not uploading {repo_id}: its {data_file} failed the leak guard")
            return False
        if dry_run:
            print("  dry run: nothing created or uploaded")
            return True
        if repo_type == "space":
            api.create_repo(repo_id=repo_id, repo_type="space", space_sdk="static", private=False, exist_ok=True)
        else:
            api.create_repo(repo_id=repo_id, repo_type="dataset", private=False, exist_ok=True)
        api.upload_folder(folder_path=str(staging), repo_id=repo_id, repo_type=repo_type,
                          commit_message=f"Publish {n_episodes} GenesisWorkbenchEnv episode(s)",
                          delete_patterns=["renders/**"])
        print(f"  uploaded to https://huggingface.co/{'spaces/' if repo_type == 'space' else 'datasets/'}{repo_id}")
        return True


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--results-dir", type=Path, default=ROOT / "results",
                    help="folder holding episodes/*.json and renders/ (default: results/)")
    ap.add_argument("--space", default=DEFAULT_REPO, help=f"Space repo id (default: {DEFAULT_REPO})")
    ap.add_argument("--dataset", default=DEFAULT_REPO, help=f"dataset repo id (default: {DEFAULT_REPO})")
    ap.add_argument("--dry-run", action="store_true",
                    help="write the local hub/ files and show what would be uploaded, without touching the Hub")
    args = ap.parse_args()

    results = args.results_dir.resolve()
    episodes = load_episodes(results)
    if not episodes:
        print(f"no episodes under {results / 'episodes'}; hub/space/episodes.json left as it is")
        return 1
    for ep in episodes:
        for call in ep.get("calls", []):
            call["image"] = image_path(call.get("image"), results)
    samples = [ep["id"] for ep in episodes if ep.get("sample")]
    if samples:
        print(f"warn: {len(samples)} episode(s) are marked sample: {', '.join(samples)}")

    SPACE_DIR.mkdir(parents=True, exist_ok=True)
    DATASET_DIR.mkdir(parents=True, exist_ok=True)
    (SPACE_DIR / "episodes.json").write_text(json.dumps(episodes, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    n_frames = copy_renders(results, SPACE_DIR / "renders")
    with (DATASET_DIR / "episodes.jsonl").open("w", encoding="utf-8") as fh:
        for ep in episodes:
            fh.write(json.dumps(dataset_row(ep), ensure_ascii=False) + "\n")
    print(f"wrote {len(episodes)} episode(s) to hub/space/episodes.json and hub/dataset/episodes.jsonl, "
          f"{n_frames} frame(s) to hub/space/renders/")

    space_files = {name: SPACE_DIR / name for name in ("index.html", "README.md", "episodes.json")}
    space_files.update(tree(SPACE_DIR, "renders"))
    dataset_files = {name: DATASET_DIR / name for name in ("README.md", "episodes.jsonl")}
    dataset_files.update({rel: path for rel, path in tree(SPACE_DIR, "renders").items()})
    showcase = SPACE_DIR.parent / "showcase.gif"
    if showcase.is_file():
        space_files["showcase.gif"] = dataset_files["showcase.gif"] = showcase
    missing = [str(p) for p in list(space_files.values()) + list(dataset_files.values()) if not p.is_file()]
    if missing:
        print("missing: " + ", ".join(missing))
        return 1

    api = None
    if not args.dry_run:
        if HfApi is None:
            print("huggingface_hub is not installed: pip install huggingface_hub")
            return 1
        api = HfApi()
    ok = publish(api, "Space", args.space, "space", space_files, "episodes.json", args.dry_run, len(episodes))
    ok = publish(api, "Dataset", args.dataset, "dataset", dataset_files, "episodes.jsonl", args.dry_run,
                 len(episodes)) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())

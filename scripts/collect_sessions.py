"""Turn contributor-workbench runs into results/sessions/*.json for the dataset.

Each run folder holds what the verifier kept (GENESIS_EPISODES_DIR: one episode JSON) and, when the contributor approved
the object, the sim-asset artifact that save_sim_asset wrote: its zip and an artifact.json with its id, version and
metadata (export them from the run's store with --export on the host that ran it).

    python scripts/collect_sessions.py --export OUT TAG...                         # on the host: artifacts + episodes
    python scripts/collect_sessions.py --raw raw/c2=claude-sonnet-5-5=scripted --raw ...   # here: results/sessions/
    python scripts/collect_sessions.py --raw episodes=claude-sonnet-5-5=scripted              # a GENESIS_EPISODES_DIR
"""

import argparse
import io
import json
import zipfile
from pathlib import Path

TASKS = {"cad_mug": "contributor-cad-mug", "cad_serving_bowl": "contributor-cad-bowl", "rcad_cupboard": "contributor-rcad-cupboard",
         "scan_mug": "contributor-scan-mug", "cad_coffee_maker": "contributor-cad-coffeemaker",
         "cad_milk_frother": "contributor-cad-frother", "knife_block": "contributor-knife-block"}
CONTRIBUTORS = {"scripted": "scripts/contributor.py, over the page's HTTP API",
                "browser": "scripts/record_session.py, through the page in Chrome (recorded)",
                "person": "a person, through the page"}


def export(out: Path, tags: list[str]) -> None:
    from agent_env.artifact import FileArtifact
    from agent_env.store.routing import namespace_routing
    home = Path.home() / "work"
    for tag in tags:
        d = out / tag
        d.mkdir(parents=True, exist_ok=True)
        episodes = sorted((home / f"episodes-{tag}").glob("*.json"))
        if not episodes:
            print(tag, "no episode")
            continue
        (d / "episode.json").write_bytes(episodes[-1].read_bytes())
        name = json.loads(episodes[-1].read_text())["object"]
        log = (home / "logs" / f"{tag}.log").read_text()
        record = next((json.loads(line.split("SIM_ASSET ", 1)[1]) for line in log.splitlines() if "SIM_ASSET " in line), None)
        if record and record.get("artifact_id"):
            with namespace_routing():
                art = FileArtifact.get(record["artifact_id"], version=record["artifact_version"])
                (d / "sim_asset.zip").write_bytes(art.load())
                (d / "artifact.json").write_text(json.dumps({"id": art.id, "version": art.version, "metadata": art.metadata}, default=str))
        bot = home / "logs" / f"{tag}-bot.log"
        if bot.is_file():
            (d / "bot.log").write_text(bot.read_text())
        print(tag, name, record)


def convert(d: Path, model: str, contributor: str, video: str | None) -> dict:
    """One session from a run folder: its episode.json, or the newest episode the verifier wrote to GENESIS_EPISODES_DIR."""
    f = d / "episode.json"
    if not f.is_file():
        found = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not found:
            raise ValueError(f"{d}: no episode JSON")
        f = found[-1]
    ep = json.loads(f.read_text())
    art = json.loads((d / "artifact.json").read_text()) if (d / "artifact.json").is_file() else None
    session = ep.get("session")
    if session is None and (d / "sim_asset.zip").is_file():
        z = zipfile.ZipFile(io.BytesIO((d / "sim_asset.zip").read_bytes()))
        s = json.loads(z.read("session.json"))
        session = {"status": "approved", "chat": s["chat"], "interactions": s["interactions"], "decision": s["decision"]}
    if session is None:
        raise ValueError(f"{d}: no session in the episode and no sim asset")
    edits = {"set_inertial", "set_material", "set_shell", "set_scale", "remove_part", "set_self_collision", "set_joint", "set_collision"}
    return {
        "id": f"{TASKS[ep['object']]}-{d.name}", "task": TASKS[ep["object"]], "object": ep["object"], "model": model,
        "contributor": CONTRIBUTORS[contributor], "video": video, "status": session["status"], "score": ep["score"],
        "passed": ep["passed"], "checks": ep["checks"], "chat": session["chat"],
        "contributor_actions": [{**{k: v for k, v in a.items() if k in ("i", "t", "action", "summary", "object_edits")},
                                 **({"result": a["result"]} if a["action"] == "lift" and "result" in a else {})}
                                for a in session["interactions"]],
        "traces": session.get("traces") or {},
        "agent_calls": [{"i": c["i"], "t": c["t"], "tool": c["tool"], "args": c["args"]} for c in ep["calls"]],
        "agent_edits": [{"tool": c["tool"], "args": c["args"]} for c in ep["calls"] if c["tool"] in edits],
        "diff": ep["diff"], "duration_s": ep["duration_s"],
        "sim_asset": art and {"id": art["id"], "version": art["version"],
                              "metadata": {k: v for k, v in art["metadata"].items() if k != "updated_at"}},
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--export", type=Path)
    ap.add_argument("tags", nargs="*")
    ap.add_argument("--raw", action="append", default=[], help="DIR=MODEL=scripted|browser|person[=VIDEO]")
    ap.add_argument("--results", default="results")
    a = ap.parse_args()
    if a.export:
        return export(a.export, a.tags)
    out = Path(a.results) / "sessions"
    out.mkdir(parents=True, exist_ok=True)
    for spec in a.raw:
        d, model, contributor, *video = spec.split("=")
        row = convert(Path(d), model, contributor, video[0] if video else None)
        (out / f"{row['id']}.json").write_text(json.dumps(row, indent=1))
        print(row["id"], row["status"], row["score"], len(row["chat"]), "messages", len(row["agent_edits"]), "edits")


if __name__ == "__main__":
    main()

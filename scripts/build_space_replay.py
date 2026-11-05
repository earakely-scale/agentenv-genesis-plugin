"""Write the Space's session replays from results/sessions/*.json: hub/space/replay/index.json and one file per
session, with the events on one clock (the contributor's actions with their Genesis traces, the chat, the agent's tool
calls and edits, the decision) and the object's state after each edit. The 3D view draws each object from
hub/space/scenes/<object>.glb, its meshes as delivered.

    python scripts/build_space_replay.py
"""

import glob
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "hub" / "space" / "replay"
EDITS = {"set_inertial", "set_material", "set_shell", "set_scale", "remove_part", "set_self_collision", "set_joint"}
TITLES = {"contributor-cad-mug": "Mug, rebuilt at 0.7× scale", "contributor-cad-bowl": "Serving bowl, rebuilt at 0.5× scale",
          "contributor-cad-frother": "Milk frother with a loose cord ring", "contributor-cad-coffeemaker": "Coffee maker, solid plastic",
          "contributor-scan-mug": "Scanned mug, loaded at water density", "contributor-rcad-cupboard": "Cupboard doors that jam",
          "contributor-knife-block": "Knife block: two knives won't go in"}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    index = []
    for f in sorted(glob.glob(str(ROOT / "results" / "sessions" / "*.json"))):
        r = json.load(open(f))
        if not r.get("traces"):
            continue
        events, scale, removed = [], 1.0, []
        for a in r["contributor_actions"]:
            events.append({"t": a["t"], "type": "action", "action": a["action"], "text": a["summary"], "i": a["i"],
                           "traced": str(a["i"]) in r["traces"], "result": a.get("result")})
        for m in r["chat"]:
            events.append({"t": m["t"], "type": {"contributor": "say", "agent": "agent", "system": "decision"}[m["from"]], "text": m["text"]})
        for c in r["agent_calls"]:
            if c["tool"] == "set_scale":
                scale = c["args"]["factor"]
            if c["tool"] == "remove_part":
                part = c["args"]["part"]
                removed = [p for p in removed if p != part] if c["args"].get("restore") else removed + [part]
            events.append({"t": c["t"], "type": "edit" if c["tool"] in EDITS else "tool", "tool": c["tool"], "args": c["args"],
                           "state": {"scale": scale, "removed": list(removed)}})
        events.sort(key=lambda e: (e["t"], {"action": 0, "say": 1, "tool": 2, "edit": 3, "agent": 4, "decision": 5}[e["type"]]))
        state, t0 = {"scale": 1.0, "removed": []}, events[0]["t"]
        for e in events:
            state = e.get("state", state)
            e["state"] = state
            e["t"] = round(e["t"] - t0, 1)  # the session's clock starts at the contributor's first action, not at deploy
        row = {k: r[k] for k in ("id", "task", "object", "model", "contributor", "status", "score", "checks", "sim_asset", "video")}
        row.update({"title": TITLES.get(r["task"], r["task"]), "scene": f"scenes/{r['object']}", "events": events, "traces": r["traces"],
                    "duration_s": max(e["t"] for e in events) + 2})
        (OUT / f"{r['id']}.json").write_text(json.dumps(row, separators=(",", ":")))
        index.append({k: row[k] for k in ("id", "task", "object", "title", "status", "score", "contributor", "video")} |
                     {"edits": sum(e["type"] == "edit" for e in events), "replies": sum(e["type"] == "agent" for e in events),
                      "asset": r["sim_asset"] and f"v{r['sim_asset']['version']}"})
        print(r["id"], len(events), "events", len(r["traces"]), "traces", (OUT / f"{r['id']}.json").stat().st_size // 1024, "KB")
    order = list(TITLES)
    index.sort(key=lambda x: (order.index(x["task"]) if x["task"] in order else 99, x["contributor"]))
    (OUT / "index.json").write_text(json.dumps(index, indent=1))


if __name__ == "__main__":
    main()

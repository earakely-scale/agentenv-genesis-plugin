"""A scripted contributor for the workbench page: it plays with the object, writes the complaint, answers the agent's
questions with what a person holding the real object knows, checks each fix in the browser the way they would, and
approves or rejects. It reads only what the page shows, never the hidden acceptance checks.

    python scripts/contributor.py --persona cad_mug --log run.log     # the page's address from the run's WORKBENCH line
    python scripts/contributor.py --persona cad_mug --url http://localhost:32771/workbench
"""

import argparse
import json
import re
import sys
import time
import urllib.request

PERSONAS = {
    "cad_mug": {
        "warmup": ["look", "lift"],
        "opening": "This mug is tiny, like an espresso cup next to my hand. It should be a normal coffee mug.",
        "facts": "It's an ordinary 12 oz coffee mug: about 9.5 cm tall and 8 cm across the rim, not counting the handle. "
                 "Ceramic, so roughly 300 to 350 g.",
        "check": "lift", "ok": lambda r: 9.0 <= r["height_cm"] <= 10.5,
        "wrong": lambda r: f"Still not right: in my hand it's {r['height_cm']} cm tall, and the real one is about 9.5 cm.",
        "confirm": ["lean"],
    },
    "cad_serving_bowl": {
        "warmup": ["look", "lift"],
        "opening": "This is supposed to be a big serving bowl for the table, but it's the size of a cereal bowl.",
        "facts": "It's a large ceramic serving bowl for the middle of the table: about 32 cm across the rim and 12 cm deep, "
                 "and it weighs around 2 kg.",
        "check": "lift", "ok": lambda r: 29 <= r["across_cm"][0] <= 35,
        "wrong": lambda r: f"Still too small: it's {r['across_cm'][0]} cm across now, and the real one is about 32 cm.",
        "confirm": ["fill"],
    },
    "rcad_cupboard": {
        "warmup": ["look", "open"],
        "opening": "One of the lower doors on the kitchen cupboard won't open, and the one next to it sticks partway.",
        "facts": "They're ordinary hinged doors, eight of them, and each should swing open freely; nothing should block them.",
        "check": "open", "ok": lambda r: all(x["fraction"] is None or x["fraction"] >= 0.9 for x in r["reach"]),
        "wrong": lambda r: "Still stuck: " + ", ".join(f"{x['joint']} opens only {x['fraction'] * 100:.0f}%" for x in r["reach"]
                                                      if x["fraction"] is not None and x["fraction"] < 0.9) + ".",
        "confirm": [],
    },
}


def call(base, path, body=None):
    req = urllib.request.Request(base + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=900) as r:
        return json.loads(r.read())


def page_from_log(path, deadline):
    while time.time() < deadline:
        try:
            m = re.search(r"WORKBENCH contributor page for \S+: (\S+)", open(path).read())
        except FileNotFoundError:
            m = None
        if m:
            return m.group(1)
        time.sleep(3)
    sys.exit("no WORKBENCH line in the log")


def wait(base, deadline, until):
    while time.time() < deadline:
        s = call(base, "/state")
        if until(s):
            return s
        time.sleep(2)
    sys.exit("timed out")


def say(base, text):
    print(f"contributor: {text}", flush=True)
    call(base, "/say", {"text": text})


def act(base, action):
    out = call(base, "/act", {"action": action})
    print(f"  [{action}] {out['summary']}", flush=True)
    return out["result"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", required=True, choices=sorted(PERSONAS))
    ap.add_argument("--url")
    ap.add_argument("--log")
    ap.add_argument("--rounds", type=int, default=7)
    ap.add_argument("--minutes", type=float, default=45)
    a = ap.parse_args()
    p, deadline = PERSONAS[a.persona], time.time() + a.minutes * 60
    base = (a.url or page_from_log(a.log, deadline)).removesuffix("/")
    print("page:", base, flush=True)
    wait(base, deadline, lambda s: s.get("object") and s["agent_state"] == "waiting for you")
    for action in p["warmup"]:
        act(base, action)
    say(base, p["opening"])
    told, seen, edits = False, 0, 0
    for _ in range(a.rounds):
        s = wait(base, deadline, lambda s: s["agent_state"] in ("waiting for you", "done")
                 and sum(m["from"] == "agent" for m in s["chat"]) > seen)
        replies = [m["text"] for m in s["chat"] if m["from"] == "agent"]
        for r in replies[seen:]:
            print(f"agent: {r}", flush=True)
        seen = len(replies)
        if s["agent_state"] == "done":
            break
        if s["edits"] > edits:
            edits = s["edits"]
            r = act(base, p["check"])
            if p["ok"](r):
                for action in p["confirm"]:
                    act(base, action)
                act(base, "look")
                print("contributor approves", flush=True)
                call(base, "/decide", {"decision": "approve", "note": "Looks and feels like the real one now."})
                return
            say(base, p["wrong"](r) if told else p["wrong"](r) + " " + p["facts"])
            told = True
        elif not told:
            say(base, p["facts"])
            told = True
        else:
            say(base, "Please go ahead and change it, then I'll try it again.")
    print("contributor rejects", flush=True)
    call(base, "/decide", {"decision": "reject", "note": "Still not right after several rounds."})


if __name__ == "__main__":
    main()

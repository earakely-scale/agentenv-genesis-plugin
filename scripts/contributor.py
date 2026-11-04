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

# What each persona knows is the real object's: the Google Scanned Objects scan the CAD was built from (CC BY 4.0).
PERSONAS = {
    "cad_mug": {
        "warmup": ["look", "lift"],
        "opening": "This mug is tiny, like an espresso cup next to my hand. It should be a normal coffee mug.",
        "facts": "It's a wide ceramic coffee mug: about 9.4 cm tall and 12 cm across the rim, 16 cm including the handle. "
                 "It weighs around 300 g.",
        "check": "lift",
        "measures": [("tall", lambda r: r["height_cm"], 8.8, 10.0, "about 9.4 cm"),
                     ("across the rim", lambda r: r["across_cm"][1], 10.5, 14.1, "about 12 cm"),
                     ("heavy", lambda r: r["mass_kg"], 0.2, 0.45, "around 300 g")],
        "confirm": ["lean"],
    },
    "cad_serving_bowl": {
        "warmup": ["look", "lift"],
        "opening": "This is supposed to be a big serving bowl for the table, but it's the size of a cereal bowl.",
        "facts": "It's a large ceramic serving bowl on a small foot: about 32 cm across and 14 to 15 cm tall, and it weighs "
                 "around 2 kg.",
        "check": "lift",
        "measures": [("across", lambda r: r["across_cm"][0], 29, 35, "about 32 cm"),
                     ("tall", lambda r: r["height_cm"], 13, 16, "14 to 15 cm"),
                     ("heavy", lambda r: r["mass_kg"], 1.2, 3.0, "around 2 kg")],
        "confirm": ["fill"],
    },
    "rcad_cupboard": {
        "warmup": ["look", "open"],
        "opening": "One of the lower doors on the kitchen cupboard won't open, and the one next to it sticks partway.",
        "facts": "They're ordinary hinged doors, eight of them, and each should swing open freely; nothing should block them.",
        "check": "open",
        "ok": lambda r: all(x["fraction"] is None or x["fraction"] >= 0.9 for x in r["reach"]),
        "wrong": lambda r: "Still stuck: " + ", ".join(f"{x['joint']} opens only {x['fraction'] * 100:.0f}%" for x in r["reach"]
                                                      if x["fraction"] is not None and x["fraction"] < 0.9) + ".",
        "confirm": [],
    },
}


def judge(p, r):
    """Whether the fix feels right, and if not, what the contributor says about it."""
    if "measures" not in p:
        return p["ok"](r), None if p["ok"](r) else p["wrong"](r)
    off = [(name, get(r), real) for name, get, lo, hi, real in p["measures"] if not lo <= get(r) <= hi]
    if not off:
        return True, None
    unit = lambda name, v: f"{v:.2f} kg" if name == "heavy" else f"{v:g} cm"
    return False, "Still not right: " + "; ".join(f"it's {unit(n, v)} {n}, and the real one is {real}" for n, v, real in off) + "."


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
    ap.add_argument("--pause", type=float, default=0, help="seconds to wait before approving, to look at the page")
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
            ok, complaint = judge(p, r)
            if ok:
                for action in p["confirm"]:
                    act(base, action)
                act(base, "look")
                time.sleep(a.pause)
                print("contributor approves", flush=True)
                call(base, "/decide", {"decision": "approve", "note": "Looks and feels like the real one now."})
                return
            say(base, complaint if told else complaint + " " + p["facts"])
            told = True
        elif not told:
            say(base, p["facts"])
            told = True
        else:
            say(base, "Please go ahead and change it, then I'll try it again.")
    time.sleep(a.pause)
    print("contributor rejects", flush=True)
    call(base, "/decide", {"decision": "reject", "note": "Still not right after several rounds."})


if __name__ == "__main__":
    main()

"""The contributor's side of the workbench: the page's actions on the object, the chat with the agent, and approval.

A contributor plays with the object at /workbench. Each action runs Genesis on the object as it is now and is logged,
so the agent reads it with last_interaction. The chat reaches the agent through an A2A peer that the workbench_session
step talks to (/workbench/peer/a2a): each of the agent's replies is a message to the peer, and the task it opens
completes with the contributor's next message, or ends canceled once they approve or reject the object."""

from __future__ import annotations

import assets as A
import base64
import copy
import io
import math
import time
import uuid

ACTIONS = {
    "look": ("Look", "Render it as it is now"),
    "lift": ("Lift", "Feel how heavy and how big it is"),
    "lean": ("Lean", "Tilt the table under it until it tips or slides"),
    "drop": ("Drop", "Drop it from twice its height"),
    "fill": ("Fill", "Drop five marbles into it"),
    "shake": ("Shake", "Check that its parts hold together"),
    "open": ("Open", "Open every door and drawer"),
    "pull": ("Pull", "Pull the drawer with 10 N"),
}
PROBE = {"lean": "tip", "drop": "drop", "fill": "cavity", "shake": "parts", "pull": "travel"}
EVERYDAY = [(0.06, "an egg"), (0.15, "an apple"), (0.33, "a can of soda"), (0.5, "a 500 ml bottle of water"),
            (1.0, "a 1 L carton of milk"), (2.0, "a 2 L bottle of soda"), (5.0, "a 5 kg bag of flour"),
            (10.0, "a full 10 L bucket of water"), (15.0, "a microwave oven"), (25.0, "a packed suitcase")]


def chart(probe: dict) -> bytes | None:
    if "x" not in probe or not probe.get("y"):
        return None
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    fig, ax = plt.subplots(figsize=(6.4, 3.4), dpi=110)
    if all(isinstance(v, str) for v in probe["x"]):
        ax.bar(range(len(probe["x"])), [v or 0 for v in probe["y"]], color="#0B7A72")
        ax.set_xticks(range(len(probe["x"])), probe["x"], rotation=35, ha="right", fontsize=7)
        ax.axhline(0.9, ls="--", color="#BF3B2E", lw=1)
    else:
        ax.plot(probe["x"], probe["y"], marker="o", ms=3.5, color="#0B7A72", lw=1.8)
    ax.set_xlabel(probe.get("x_label", ""), fontsize=9)
    ax.set_ylabel(probe.get("y_label", ""), fontsize=9)
    ax.set_title(f"{probe['kind']} probe", loc="left", fontsize=10)
    ax.grid(alpha=0.3)
    fig.tight_layout()
    buf = io.BytesIO()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()


def actions_for(obj) -> list[dict]:
    if obj.kind == "mesh":
        keys = ["look", "lift", "lean", "drop", "fill", "shake"]
    elif obj.kind == "urdf":
        keys = ["look", "open"]
    else:
        keys = ["look"] + {"tip": ["lift", "lean"], "hinge": ["open"], "travel": ["pull"]}[obj.model["probe"]]
    return [{"id": k, "label": ACTIONS[k][0], "hint": ACTIONS[k][1]} for k in keys]


def label(obj) -> str:
    return obj.model.get("label") if obj.kind == "mesh" else obj.spec["label"] if obj.kind == "urdf" else obj.name.split("_")[0]


def description(obj) -> str:
    return obj.model["description"] if obj.kind in ("mesh", "primitive") else obj.spec["description"]


def _heft(kg: float) -> str:
    return min(EVERYDAY, key=lambda e: abs(math.log(kg / e[0])))[1]


def _lift(obj) -> tuple[str, dict]:
    if obj.kind == "mesh":
        s = obj.summary()
        mass, (x, y, z) = s["mass_kg"], s["bbox_m"]
        text = (f"It weighs {mass:.2f} kg, about as heavy as {_heft(mass)}. It stands {z * 100:.1f} cm tall and is "
                f"{max(x, y) * 100:.1f} cm by {min(x, y) * 100:.1f} cm across.")
        return text, {"mass_kg": mass, "height_cm": round(z * 100, 1), "across_cm": [round(max(x, y) * 100, 1), round(min(x, y) * 100, 1)]}
    mass = sum(L["mass"] for L in obj.model["links"].values() if not L.get("fixed"))
    return f"It weighs {mass:.2f} kg, about as heavy as {_heft(mass)}.", {"mass_kg": round(mass, 3)}


def summarize(kind: str, r: dict) -> str:
    if r.get("error"):
        return {"hinge": "The door won't open at all: it seems welded shut.",
                "travel": "The drawer doesn't move at all."}.get(kind, r["error"])
    if kind == "tip":
        a = r.get("first_failure_deg", r.get("tips_at_deg"))
        if a is None:
            return "On a table tilted slowly up to 70°, it stays put."
        return f"On a slowly tilting table it {'slides off' if r.get('mode') == 'slide' else 'tips over'} at {a:g}°."
    if kind == "drop":
        rest = f"comes to rest after {r['settles_s']} s" if r.get("settles_s") else "is still moving after 2.4 s"
        tilt = r.get("final_tilt_deg") or 0
        return (f"Dropped from twice its height, it {rest} and ends {'upright' if tilt < 10 else f'tilted {tilt:g}°'}; "
                f"at worst it sinks {r['max_penetration_mm']} mm into the floor.")
    if kind == "cavity":
        return f"{r['balls_inside']} of 5 marbles dropped into it stay inside" + ("." if r["open"] else "; the opening seems closed off.")
    if kind == "parts":
        if not r["floating"]:
            return "Everything holds together; no part is loose."
        gaps = r["gap_to_nearest_part_mm"]
        return "Some parts aren't attached to anything: " + ", ".join(f"{p} is {gaps[p]:g} mm from the nearest part" for p in r["floating"]) + "."
    if kind == "joints":
        stuck = [x for x in r["reach"] if x["fraction"] is not None and x["fraction"] < 0.9]
        n = len(r["reach"])
        if not stuck:
            return f"Every door and drawer ({n}) opens fully."
        return (f"{n - len(stuck)} of {n} doors and drawers open fully; "
                + ", ".join(f"{x['joint']} gets only {max(x['fraction'], 0) * 100:.0f}% of the way" for x in stuck) + ".")
    if kind == "hinge":
        top = max(r["y"])
        when = f" within {r['opens_at_s']} s" if r.get("opens_at_s") is not None else ""
        return f"Unlatched, the door swings to {top:.0f}°{when}."
    if kind == "travel":
        return f"Pulled with 10 N, the drawer slides out {r['max_m'] * 100:.0f} cm."
    return f"{kind}: done."


def run_action(obj, action: str, last_probe: dict | None) -> dict:
    """One contributor action on ``obj`` as it is now: what they'd notice, the numbers, and an image."""
    if action not in {a["id"] for a in actions_for(obj)}:
        raise ValueError(f"{action!r} doesn't apply to {obj.name}")
    if action == "shake" and obj.kind == "mesh":
        r = obj.probe("parts")
        return {"summary": summarize("parts", r), "result": r, "png": obj.render(last_probe),
                "trace": {"kind": "parts", "floating": r["floating"], "gaps_mm": r["gap_to_nearest_part_mm"]}}
    if action == "look":
        return {"summary": f"Looked at the {label(obj)}.", "result": {}, "png": obj.render(last_probe)}
    if action == "lift":
        text, numbers = _lift(obj)
        return {"summary": text, "result": numbers, "png": obj.render(last_probe)}
    kind = "joints" if action == "open" and obj.kind == "urdf" else "hinge" if action == "open" else PROBE[action]
    traced = obj.kind in ("mesh", "urdf")
    if traced:
        obj._trace = {"frames": [], "t": []}
    try:
        r = obj.probe(kind)
    finally:
        raw, obj._trace = getattr(obj, "_trace", None), None
    trace = None
    if traced and raw and raw["frames"]:
        env = 0
        if kind == "tip":
            env = next((i for i, m in enumerate(r["modes"]) if m != "holds"), len(r["modes"]) - 1)
        trace = {"kind": kind, **A.finish_trace(raw, env)}
        if kind == "tip":
            trace["lean_deg"] = r["x"][env]
        if kind == "parts":
            trace["floating"] = r["floating"]
    return {"summary": summarize(kind, r), "result": r, "png": chart(r) or obj.render(r), "probe": r, "trace": trace}


def final_report(obj) -> dict:
    """What an approval attaches: the hidden acceptance checks and every probe the object takes, on the approved object."""
    rows, probes, _ = obj.checks("current")
    for kind in {"mesh": ("rest", "drop", "tip", "parts"), "urdf": ("joints",)}.get(obj.kind, ()):
        if kind not in probes:
            probes[kind] = obj.probe(kind)
    return {"acceptance": rows, "passed": all(r["result"] for r in rows), "probes": probes}


class Session:
    def __init__(self) -> None:
        self.t0 = time.time()
        self.status = "open"
        self.chat: list[dict] = []
        self.actions: list[dict] = []
        self.unread: list[str] = []
        self.told = 0
        self.peer: dict[str, dict] = {}
        self.connected = False
        self.decision: dict | None = None
        self.report = None
        self.view: tuple[int, bytes] | None = None
        self.traces: dict[int, dict] = {}
        self.scenes: dict[str, tuple] = {}

    def now(self) -> float:
        return round(time.time() - self.t0, 1)

    def say(self, who: str, text: str) -> dict:
        entry = {"i": len(self.chat) + 1, "t": self.now(), "from": who, "text": text}
        self.chat.append(entry)
        if who == "contributor":
            self.unread.append(text)
        return entry

    def log_action(self, action: str, out: dict, rev: int) -> dict:
        entry = {"i": len(self.actions) + 1, "t": self.now(), "action": action, "summary": out["summary"],
                 "result": out["result"], "object_edits": rev,
                 "image_png_b64": base64.b64encode(out["png"]).decode() if out.get("png") else None}
        if out.get("trace"):
            self.traces[entry["i"]] = out["trace"]
        self.actions.append(entry)
        return entry

    def decide(self, status: str, note: str, obj, rev: int) -> None:
        if self.status != "open":
            raise ValueError(f"the object was already {self.status}")
        self.status = status
        self.decision = {"status": status, "note": note, "t": self.now(), "object_edits": rev,
                         "model": copy.deepcopy(obj.model)}
        self.say("system", f"The contributor {status} the object" + (f": {note}" if note else "."))

    def agent_state(self) -> str:
        if not self.connected:
            return "not connected"
        if self.status != "open":
            return "done"
        return "waiting for you" if any("final" not in t for t in self.peer.values()) else "working"

    # A2A peer (JSON-RPC at /workbench/peer/a2a)
    def rpc(self, body: dict) -> dict:
        rid, method, p = body.get("id"), body.get("method"), body.get("params") or {}
        if method == "message/send":
            msg = p.get("message") or {}
            parts = msg.get("parts") or []
            self.connected = True
            opening = any(x.get("kind") == "data" and (x.get("data") or {}).get("workbench") == "open" for x in parts)
            text = "\n".join(x.get("text", "") for x in parts if x.get("kind") == "text").strip()
            if text and not opening:
                self.say("agent", text)
            tid = uuid.uuid4().hex
            self.peer[tid] = {"contextId": msg.get("contextId") or tid}
            return {"jsonrpc": "2.0", "id": rid, "result": self._task(tid)}
        if method in ("tasks/get", "tasks/cancel"):
            tid = p.get("id")
            if tid not in self.peer:
                return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32001, "message": f"no task {tid}"}}
            if method == "tasks/cancel" and "final" not in self.peer[tid]:
                self.peer[tid]["final"] = {"state": "canceled"}
            return {"jsonrpc": "2.0", "id": rid, "result": self._task(tid)}
        return {"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unsupported method {method}"}}

    def _task(self, tid: str) -> dict:
        t = self.peer[tid]
        if "final" not in t:
            if self.status != "open":
                t["final"] = {"state": "canceled", "message": self._message(f"The contributor {self.status} the object.")}
            elif self.unread:
                t["final"] = {"state": "completed", "message": self._message(self._compose())}
        status = t.get("final") or {"state": "working"}
        return {"kind": "task", "id": tid, "contextId": t["contextId"], "status": status}

    def _compose(self) -> str:
        text = "\n\n".join(self.unread)
        self.unread = []
        new, self.told = self.actions[self.told:], len(self.actions)
        if new:
            text += ("\n\n(In the workbench since your last message, the contributor ran: "
                     + "; ".join(f"{a['action']}: {a['summary'].rstrip('.')}" for a in new)
                     + ". last_interaction shows these with their numbers and images.)")
        return text

    @staticmethod
    def _message(text: str) -> dict:
        return {"kind": "message", "role": "user", "messageId": uuid.uuid4().hex, "parts": [{"kind": "text", "text": text}]}

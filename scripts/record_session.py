"""Play a contributor through the workbench page in a real browser, and record it as a video.

The same personas as contributor.py, but every step goes through the page as a person's would: clicking the actions,
typing into the chat, approving with a note. Captions on the page say what the contributor is doing. While the agent
works the video runs faster, and says so. Needs Playwright (with the installed Chrome) and ffmpeg.

    python scripts/record_session.py --persona cad_mug --url http://localhost:8765/workbench --out videos/cad_mug.mp4
"""

import argparse
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parent))
from contributor import PERSONAS, judge  # noqa: E402

FAST = 8
CAPTION = """([text, fast]) => {
  let c = document.getElementById('rec-caption');
  if (!c) { c = document.createElement('div'); c.id = 'rec-caption'; document.body.append(c);
    c.style.cssText = 'position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:9;padding:9px 16px;border-radius:99px;' +
      'font:600 15px system-ui,sans-serif;color:#fff;box-shadow:0 4px 18px rgba(0,0,0,.25);max-width:80%;text-align:center'; }
  c.textContent = text; c.style.background = fast ? '#6a6f76' : '#0b7a72'; c.style.display = text ? 'block' : 'none';
}"""


class Recorder:
    def __init__(self, page):
        self.page, self.t0, self.segments, self.fast_from = page, time.time(), [], None

    def now(self):
        return time.time() - self.t0

    def caption(self, text, fast=False):
        self.page.evaluate(CAPTION, [text, fast])
        if fast and self.fast_from is None:
            self.fast_from = self.now()
        if not fast and self.fast_from is not None:
            self.segments.append((self.fast_from, self.now()))
            self.fast_from = None

    def state(self):
        return self.page.evaluate("fetch(location.pathname.replace(/\\/+$/, '') + '/state').then(r => r.json())")

    def pause(self, ms=1400):
        self.page.wait_for_timeout(ms)

    def act(self, action, why):
        self.caption(why)
        self.pause(900)
        self.page.click(f'#actions button[data-action="{action}"]')
        self.page.wait_for_function("document.getElementById('busy').style.display !== 'block'", timeout=600_000)
        self.pause(2200)
        last = self.state()["last"]
        print(f"  [{action}] {last['summary']}", flush=True)
        return last["result"]

    def say(self, text):
        self.caption("The contributor writes to the agent")
        self.page.click("#text")
        self.page.type("#text", text, delay=12)
        self.pause(500)
        self.page.click("#send")
        print(f"contributor: {text}", flush=True)
        self.pause(800)

    def wait_agent(self, seen):
        self.caption(f"The agent is working: inspecting, editing and probing in Genesis (video at {FAST}× speed)", fast=True)
        deadline = time.time() + 1800
        while time.time() < deadline:
            s = self.state()
            if s["agent_state"] in ("waiting for you", "done") and sum(m["from"] == "agent" for m in s["chat"]) > seen:
                self.caption("The agent replied")
                self.pause(5000)
                return s
            self.page.wait_for_timeout(1000)
        raise TimeoutError("the agent never replied")


def play(rec, p, rounds=6):
    rec.state()
    while rec.state()["agent_state"] != "waiting for you":
        rec.pause(1000)
    rec.caption("A contributor opens the object in the workbench")
    rec.pause(3500)
    names = {"look": "looks at it", "lift": "lifts it", "lean": "leans it on a tilting table", "drop": "drops it",
             "fill": "drops marbles into it", "shake": "shakes it to see what's loose", "open": "opens every door"}
    for action in p["warmup"]:
        rec.act(action, f"The contributor {names[action]}")
    rec.say(p["opening"])
    told, seen, edits = False, 0, 0
    for _ in range(rounds):
        s = rec.wait_agent(seen)
        seen = sum(m["from"] == "agent" for m in s["chat"])
        if s["agent_state"] == "done":
            return "done"
        if s["edits"] > edits:
            edits = s["edits"]
            r = rec.act(p["check"], f"The contributor {names[p['check']]} again to check the fix")
            ok, complaint = judge(p, r)
            if ok:
                for action in p["confirm"]:
                    rec.act(action, f"The contributor {names[action]}")
                rec.act("look", "The contributor takes a last look")
                rec.caption("It feels like the real one: the contributor approves it")
                rec.page.fill("#note", "Looks and feels like the real one now.")
                rec.pause(1200)
                rec.page.click("#approve")
                print("contributor approves", flush=True)
                rec.pause(4500)
                rec.caption("")
                rec.pause(1500)
                return "approved"
            rec.say(complaint if told else complaint + " " + p["facts"])
            told = True
        elif not told:
            rec.say(p["facts"])
            told = True
        else:
            rec.say("Please go ahead and change it, then I'll try it again.")
    rec.page.fill("#note", "Still not right after several rounds.")
    rec.page.click("#reject")
    rec.pause(3000)
    return "rejected"


def encode(webm: Path, out: Path, fast: list[tuple[float, float]], length: float) -> None:
    """The recording at normal speed, with the agent's working stretches FAST times faster."""
    cuts, t = [], 0.0
    for a, b in fast:
        if a > t:
            cuts.append((t, a, 1))
        cuts.append((a, b, FAST))
        t = b
    cuts.append((t, length, 1))
    parts = [f"[0:v]trim=start={a:.2f}:end={b:.2f},setpts=(PTS-STARTPTS)/{k}[v{i}]" for i, (a, b, k) in enumerate(cuts) if b - a > 0.05]
    graph = ";".join(parts) + ";" + "".join(f"[v{i}]" for i in range(len(parts))) + f"concat=n={len(parts)}:v=1:a=0,fps=25[out]"
    out.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(webm), "-filter_complex", graph, "-map", "[out]",
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", "-crf", "26", "-movflags", "+faststart", str(out)], check=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--persona", required=True, choices=sorted(PERSONAS))
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", required=True, type=Path)
    a = ap.parse_args()
    tmp = Path(tempfile.mkdtemp(prefix="wb-rec-"))
    size = {"width": 1360, "height": 860}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(channel="chrome", headless=True)
        ctx = browser.new_context(viewport=size, record_video_dir=str(tmp), record_video_size=size)
        page = ctx.new_page()
        page.goto(a.url)
        rec = Recorder(page)
        outcome = play(rec, PERSONAS[a.persona])
        length = rec.now()
        video = page.video.path()
        ctx.close()
        browser.close()
    encode(Path(video), a.out, rec.segments, length)
    shutil.rmtree(tmp, ignore_errors=True)
    print(outcome, a.out, f"{length:.0f}s recorded", flush=True)


if __name__ == "__main__":
    main()

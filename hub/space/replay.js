// Session replay: one contributor session on one clock. The 3D view shows the object as it was at the selected event
// (rescaled and with removed parts hidden, per the agent's edits so far) and replays the Genesis trace of each of the
// contributor's actions; the timeline and the transcript select events.
import { SimViewer } from "./viewer.js";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
const esc = (t) => t.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
const inline = (t) => esc(t).replace(/`([^`]+)`/g, "<code>$1</code>").replace(/\*\*([^*]+)\*\*/g, "<b>$1</b>");
function markdown(text) {
  const out = []; let list = null, table = false;
  const close = () => { if (list) out.push(`</${list}>`); if (table) out.push("</table>"); list = null; table = false; };
  for (const raw of text.split("\n")) {
    const line = raw.trimEnd(), b = line.match(/^\s*[-*] (.*)/), n = line.match(/^\s*\d+[.)] (.*)/);
    if (/^\|.*\|$/.test(line)) {
      if (/^\|[\s:|-]+\|$/.test(line)) continue;
      if (!table) { close(); out.push("<table>"); table = true; }
      out.push("<tr>" + line.slice(1, -1).split("|").map((c) => `<td>${inline(c.trim())}</td>`).join("") + "</tr>");
    } else if (b || n) {
      const k = b ? "ul" : "ol";
      if (list !== k) { close(); out.push(`<${k}>`); list = k; }
      out.push(`<li>${inline((b || n)[1])}</li>`);
    } else if (!line.trim()) close();
    else { close(); out.push(`<p>${inline(line)}</p>`); }
  }
  close();
  return out.join("");
}
const fmt = (a) => Object.entries(a).map(([k, v]) => `${k}=${typeof v === "number" ? +v.toFixed(4) : v}`).join(", ");
const VERB = { look: "looks at it", lift: "lifts it", lean: "leans it on a tilting table", drop: "drops it", fill: "drops marbles in",
  shake: "shakes it", open: "opens every door", pull: "pulls the drawer", insert: "drops every knife into its slot",
  extract: "pulls each knife out" };
const KIND = { action: "contributor action", say: "contributor", agent: "agent", edit: "agent edit", tool: "agent probe", decision: "decision" };

export async function mount(root) {
  const index = await (await fetch("replay/index.json")).json();
  const viewer = new SimViewer($("rp-view"), { onFrame: (st) => { $("rp-label").hidden = !(st && st.label); $("rp-label").textContent = (st && st.label) || ""; } });
  let row = null, cur = -1, playing = false, token = 0;

  for (const s of index) {
    const b = el("button", "rp-tab");
    b.setAttribute("role", "tab");
    b.append(el("b", "", s.title), el("span", "", `${s.contributor.startsWith("a person") ? "person" : s.contributor.includes("Chrome") ? "browser" : "scripted"} · ${s.status} · ${s.score.toFixed(2)} · ${s.engine === "mujoco" ? "MuJoCo" : "Genesis"}`));
    b.onclick = () => select(s.id);
    b.dataset.id = s.id;
    $("rp-tabs").append(b);
  }

  async function select(id) {
    stop();
    for (const b of $("rp-tabs").children) b.setAttribute("aria-selected", b.dataset.id === id);
    row = await (await fetch(`replay/${id}.json`)).json();
    const meta = await (await fetch(`${row.scene}.json`)).json();
    await viewer.load(`${row.scene}.glb`, meta, { url: `${row.scene}.glb`, meta });
    $("rp-grid").textContent = viewer.gridLabel;
    drawTimeline();
    drawLog();
    drawFacts();
    cur = -1;
    goTo(0, false);
  }

  function stateText(s) {
    const bits = [];
    if (s.scale !== 1) bits.push(`scale ×${(+s.scale).toFixed(3)}`);
    if (s.removed.length) bits.push(`removed: ${s.removed.join(", ")}`);
    const per = Object.entries(s.collision || {}).filter(([, m]) => m === "parts").map(([b]) => b.split("_")[0]);
    if (per.length) bits.push(`collision per part: ${per.join(", ")}`);
    return bits.join(" · ") || "as delivered";
  }

  function goTo(i, animate = true) {
    if (!row || i < 0 || i >= row.events.length) return;
    cur = i;
    const e = row.events[i];
    viewer.reset();
    viewer.setState(e.state);
    $("rp-state").textContent = row.object.startsWith("rcad") ? (e.state && row.events.slice(0, i + 1).some((x) => x.tool === "set_self_collision") ? "self-collision off" : "as delivered") : stateText(e.state);
    if (e.type === "action" && animate) {
      if (e.action === "lift") viewer.play({ kind: "lift" });
      else if (e.traced) viewer.play(row.traces[String(e.i)]);
    }
    for (const n of $("rp-log").children) n.classList.toggle("on", +n.dataset.i === i);
    const item = $("rp-log").querySelector(`[data-i="${i}"]`);
    if (item) item.scrollIntoView({ block: "nearest", behavior: "smooth" });
    for (const m of $("rp-marks").children) m.classList.toggle("on", +m.dataset.i === i);
    $("rp-needle").style.left = `${(e.t / row.duration_s) * 100}%`;
    $("rp-time").textContent = `${e.t.toFixed(0)} s / ${row.duration_s.toFixed(0)} s · ${KIND[e.type]}`;
  }

  function drawTimeline() {
    const box = $("rp-marks");
    box.textContent = "";
    row.events.forEach((e, i) => {
      if (e.type === "tool" && e.tool === "last_interaction") return;
      const m = el("button", `rp-mark ${e.type}`);
      m.style.left = `${(e.t / row.duration_s) * 100}%`;
      m.dataset.i = i;
      m.title = e.type === "action" ? `${e.t.toFixed(0)} s · ${e.action}: ${e.text}` : e.type === "edit" || e.type === "tool" ? `${e.t.toFixed(0)} s · ${e.tool}(${fmt(e.args)})` : `${e.t.toFixed(0)} s · ${KIND[e.type]}`;
      m.setAttribute("aria-label", m.title);
      m.onclick = () => { stop(); goTo(i); };
      box.append(m);
    });
  }

  function drawLog() {
    const box = $("rp-log");
    box.textContent = "";
    row.events.forEach((e, i) => {
      if (e.type === "tool" && e.tool === "last_interaction") return;
      const li = el("li", `rp-ev ${e.type}`);
      li.dataset.i = i;
      li.append(el("span", "rp-t", `${e.t.toFixed(0)}s`));
      const body = el("div", "rp-body");
      if (e.type === "action") body.append(el("b", "", `Contributor ${VERB[e.action] || e.action}`), el("div", "", e.text));
      else if (e.type === "say") body.append(el("b", "", "Contributor"), el("div", "", e.text));
      else if (e.type === "agent") { body.append(el("b", "", "Agent")); const d = el("div", "md"); d.innerHTML = markdown(e.text); body.append(d); }
      else if (e.type === "decision") body.append(el("b", "", e.text));
      else body.append(el("code", "", `${e.tool}(${fmt(e.args)})`));
      li.append(body);
      li.onclick = () => { stop(); goTo(i); };
      box.append(li);
    });
  }

  function drawFacts() {
    const f = $("rp-facts");
    f.textContent = "";
    const add = (k, v) => { const d = el("div"); d.append(el("span", "", k), el("b", "", v)); f.append(d); };
    add("Outcome", `${row.status}, grade ${row.score.toFixed(2)}`);
    add("Saved as", row.sim_asset ? `${row.sim_asset.id.split("/").pop()} v${row.sim_asset.version}` : "not saved");
    add("Hidden checks", row.checks.map((c) => `${c.result ? "✓" : "✗"} ${c.id}`).join("  "));
    add("Contributor", row.contributor.startsWith("a person") ? "a person, through the page"
      : row.contributor.includes("Chrome") ? "scripted, through the page in Chrome" : "scripted, over the page's API");
    if (row.video) { const d = el("div"); const a = el("a", "", "Watch the recording"); a.href = row.video; a.target = "_blank"; d.append(el("span", "", "Video"), a); f.append(d); }
  }

  const wait = (ms, t) => new Promise((r) => setTimeout(() => r(t === token), ms));
  async function play() {
    if (!row) return;
    playing = true;
    const t = ++token;
    $("rp-play").textContent = "Pause";
    for (let i = cur + 1 >= row.events.length ? 0 : Math.max(cur, 0); i < row.events.length; i++) {
      const e = row.events[i];
      if (e.type === "tool" && e.tool === "last_interaction") continue;
      goTo(i);
      let ms = 900;
      if (e.type === "action") ms = e.action === "lift" ? 2700 : e.traced ? (viewer.duration() / viewer.speed) * 1000 + 900 : 1400;
      else if (e.type === "say" || e.type === "agent") ms = Math.min(6500, 1600 + e.text.length * 14);
      else if (e.type === "decision") ms = 3000;
      else if (e.type === "tool") ms = 450;
      if (!(await wait(ms, t))) return;
    }
    stop();
  }
  function stop() { playing = false; token++; $("rp-play").textContent = "Play session"; }

  $("rp-play").onclick = () => (playing ? stop() : play());
  $("rp-prev").onclick = () => { stop(); goTo(Math.max(0, cur - 1)); };
  $("rp-next").onclick = () => { stop(); goTo(Math.min(row.events.length - 1, cur + 1)); };
  $("rp-speed").onchange = (e) => (viewer.speed = +e.target.value);
  $("rp-ghost").onchange = (e) => viewer.setGhost(e.target.checked);
  $("rp-track").onclick = (ev) => {
    if (!row || ev.target.closest(".rp-mark")) return;
    const r = $("rp-track").getBoundingClientRect(), t = ((ev.clientX - r.left) / r.width) * row.duration_s;
    let best = 0;
    row.events.forEach((e, i) => { if (e.t <= t) best = i; });
    stop(); goTo(best);
  };
  await select(index[0].id);
}

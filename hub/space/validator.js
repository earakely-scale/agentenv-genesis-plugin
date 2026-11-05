// The MJCF validator's batch: every asset it checked, its settle and drop replayed in 3D from MuJoCo's traces, and each
// check with what it measured (scripts/validate_mjcf.py).
import { SimViewer } from "./viewer.js";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
const ICON = { pass: "✓", warn: "⚠", fail: "✗" };
const CLS = { pass: "ok", warn: "warn", fail: "bad" };
const RUNS = { settle: "Settle on the floor", drop: "Drop from 10 cm" };

export async function mount() {
  const index = await (await fetch("validator/index.json")).json();
  const v = new SimViewer($("va-view"), { onFrame: (st) => { $("va-label").hidden = !(st && st.label); $("va-label").textContent = (st && st.label) || ""; } });
  let report = null, run = "settle";
  const counts = { pass: 0, warn: 0, fail: 0 };
  index.forEach((a) => { counts[a.verdict] += 1; });
  $("va-sum").textContent = `${index.length} assets: ${counts.fail} fail, ${counts.warn} pass with warnings, ${counts.pass} pass.`;

  index.forEach((a, i) => {
    const b = el("button", "btn va-asset");
    b.append(el("span", `va-dot ${CLS[a.verdict]}`, ICON[a.verdict]), el("span", "", a.name.replace(/_/g, " ")));
    b.title = [a.fails.length ? `fails: ${a.fails.join(", ")}` : "", a.warns.length ? `warns: ${a.warns.join(", ")}` : ""].filter(Boolean).join("; ");
    b.onclick = () => open(i);
    $("va-assets").append(b);
  });
  for (const [k, lab] of Object.entries(RUNS)) {
    const b = el("button", "btn", lab);
    b.dataset.k = k;
    b.onclick = () => { run = k; play(); };
    $("va-runs").append(b);
  }
  $("va-replay").onclick = () => play();

  function play() {
    [...$("va-runs").children].forEach((b) => b.classList.toggle("pri", b.dataset.k === run));
    const t = report && report.traces && report.traces[run];
    if (t) v.play(t);
  }

  async function open(i) {
    const a = index[i];
    [...$("va-assets").children].forEach((b, j) => b.classList.toggle("pri", j === i));
    report = await (await fetch(`validator/${a.file}`)).json();
    if (report.scene) {
      const meta = await (await fetch(`validator/${report.scene.replace(/\.glb$/, ".scene.json")}`)).json();
      await v.load(`validator/${report.scene}`, meta);
      $("va-grid").textContent = v.gridLabel;
    }
    $("va-title").textContent = report.name.replace(/_/g, " ");
    $("va-facts").textContent = report.bodies !== undefined
      ? `${report.mjcf}: ${report.bodies} ${report.bodies === 1 ? "body" : "bodies"}, ${report.mass_kg} kg, ${report.joints} ${report.joints === 1 ? "joint" : "joints"}, a ${(report.timestep * 1000).toFixed(1)} ms step` + (report.floor_added ? ", on a floor the validator adds" : "")
      : report.mjcf;
    const t = $("va-checks");
    t.textContent = "";
    const head = el("tr");
    for (const h of ["", "Check", "What it found"]) head.append(el("th", "", h));
    t.append(head);
    for (const c of report.checks) {
      const tr = el("tr");
      tr.append(el("td", CLS[c.level], ICON[c.level]), el("td", "", c.id), el("td", "", c.message));
      t.append(tr);
    }
    $("va-verdict").textContent = `${report.verdict === "pass" ? "Passes every check" : report.verdict === "warn" ? "Passes, with warnings" : "Fails"}: ` +
      `${report.checks.filter((c) => c.level === "fail").length} fail, ${report.checks.filter((c) => c.level === "warn").length} warn, ${report.checks.filter((c) => c.level === "pass").length} pass`;
    $("va-verdict").className = "vr-verdict " + (report.verdict === "fail" ? "bad" : "ok");
    $("va-md").href = `validator/${a.file.replace(/\.json$/, ".md")}`;
    // a scene with furniture isn't dropped, so it has no drop to replay
    [...$("va-runs").children].forEach((b) => { b.disabled = !(report.traces && report.traces[b.dataset.k]); });
    if (!(report.traces && report.traces[run])) run = "settle";
    play();
  }
  await open(Math.max(0, index.findIndex((a) => a.verdict === "fail")));
}

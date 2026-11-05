// A set's validation report: each probe's MuJoCo trace replayed in 3D, as delivered and after the fix, beside the
// hidden checks in MuJoCo and Genesis, the static tipping margin, the per-knife numbers, how far anything went into
// anything else, and the MJCF + OBJ export.
import { SimViewer } from "./viewer.js";

const $ = (id) => document.getElementById(id);
const el = (tag, cls, text) => { const e = document.createElement(tag); if (cls) e.className = cls; if (text !== undefined) e.textContent = text; return e; };
const PROBES = { rest: "Rest", seat: "Insert", extract: "Pull", tip: "Lean" };
const ABOUT = {
  rest: "Every body starts at its designed pose and settles for 1.5 s: how far each knife ends from its seat.",
  seat: "Each knife is dropped into its slot from 2 cm above: does it go all the way in?",
  extract: "Each knife in turn is pulled out along its slot, the force rising to 3 times its weight: when does it start to come out, and does the block lift?",
  tip: "The table tilts toward the block's back, the way it and the handles lean, in 5° steps: the first angle where something gives.",
};

export async function mount(name) {
  const r = await (await fetch(`validation/${name}.json`)).json();
  const meta = await (await fetch(`scenes/${name}.json`)).json();
  const v = new SimViewer($("vr-view"), { onFrame: (st) => { $("vr-label").hidden = !(st && st.label); $("vr-label").textContent = (st && st.label) || ""; } });
  await v.load(`scenes/${name}.glb`, meta);
  $("vr-grid").textContent = v.gridLabel;
  let state = 0, probe = "seat";

  r.states.forEach((s, i) => {
    const b = el("button", "btn", s.label);
    b.onclick = () => { state = i; draw(); };
    $("vr-states").append(b);
  });
  for (const [k, lab] of Object.entries(PROBES)) {
    const b = el("button", "btn", lab);
    b.dataset.k = k;
    b.onclick = () => { probe = k; draw(); };
    $("vr-probes").append(b);
  }
  $("vr-replay").onclick = () => v.play(r.states[state].probes[probe].trace);
  $("vr-export").href = r.export;
  const names = Object.fromEntries(r.bodies.map((b) => [b.name, b.label || b.name]));
  names.floor = "the floor";
  const say = (o) => `${names[o.body] || o.body} ${o.mm.toFixed(1)} mm into ${names[o.into] || o.into}`;

  // how far anything went into anything else in the run on screen (for the lean, the angle replayed), with a button
  // that stops the replay at that moment and lights up the body
  function overlap(p) {
    const box = $("vr-overlap");
    box.textContent = "";
    const ov = p.trace.kind === "tip" ? (p.result.rows.find((x) => x.deg === p.trace.lean_deg) || {}).overlap : p.result.overlap;
    if (!ov) return;
    const deep = ov.pairs[0], end = ov.end.pairs[0];
    box.append(el("b", "", "Overlap. "));
    if (!deep) { box.append("Nothing goes into anything else in this run."); return; }
    box.append(`Deepest: ${say(deep)}, at ${deep.t.toFixed(2)} s. `);
    const b = el("button", "btn", "Show it");
    b.onclick = () => {
      v.seek(deep.t, true);
      v.playing = false;
      v.highlight(v.meta.geoms.filter((g) => g.link === deep.body).map((g) => g.part));
    };
    box.append(b, ` Once it settles: ${end ? say(end) : "nothing is inside anything else"}.`);
  }

  function draw() {
    const s = r.states[state], p = s.probes[probe];
    [...$("vr-states").children].forEach((b, i) => b.classList.toggle("pri", i === state));
    [...$("vr-probes").children].forEach((b) => b.classList.toggle("pri", b.dataset.k === probe));
    $("vr-about").textContent = ABOUT[probe];
    v.play(p.trace);
    // checks: MuJoCo grades; Genesis gives a second opinion on the checks it runs
    const g = Object.fromEntries(s.second_opinion.checks.map((c) => [c.id, c.result]));
    const t = $("vr-checks");
    t.textContent = "";
    const head = el("tr");
    for (const h of ["Check", "MuJoCo", "Genesis", "What it measured"]) head.append(el("th", "", h));
    t.append(head);
    for (const c of s.checks) {
      const tr = el("tr");
      tr.append(el("td", "", c.id), el("td", c.result ? "ok" : "bad", c.result ? "pass" : "fail"),
        el("td", c.id in g ? (g[c.id] ? "ok" : "bad") : "na", c.id in g ? (g[c.id] ? "pass" : "fail") : "not run"), el("td", "", c.description));
      t.append(tr);
    }
    $("vr-verdict").textContent = `${s.label}: ${s.passed ? "passes every check" : `fails ${s.checks.filter((c) => !c.result).length} of ${s.checks.length}`}` +
      (s.second_opinion.disagree.length ? `; Genesis disagrees on ${s.second_opinion.disagree.join(", ")}` : "; Genesis agrees wherever it runs");
    $("vr-verdict").className = "vr-verdict " + (s.passed ? "ok" : "bad");
    // per knife, for this probe
    const k = $("vr-knives");
    k.textContent = "";
    const res = p.result;
    const cols = probe === "extract" ? ["Knife", "Starts to come out at", "Block lift"] : probe === "tip" ? ["Tilt", "What happens", "Block tilt", "Left inside, at rest"] : ["Knife", "Out of its slot", "Turned"];
    const hr = el("tr");
    cols.forEach((c) => hr.append(el("th", "", c)));
    k.append(hr);
    if (probe === "tip") {
      for (const row of res.rows.slice(0, 6)) {
        const tr = el("tr");
        const e = row.overlap && row.overlap.end.pairs[0];
        tr.append(el("td", "", `${row.deg}°`), el("td", row.mode === "holds" ? "ok" : "bad", row.mode.replace(/_/g, " ")), el("td", "", `${row.block_tilt_deg}°`),
          el("td", e && e.mm > 0.5 ? "bad" : "", e ? `${e.mm.toFixed(1)} mm` : "none"));
        k.append(tr);
      }
    } else {
      for (const [n, x] of Object.entries(res.knives)) {
        const tr = el("tr");
        const lab = (res.labels && res.labels[n]) || n;
        if (probe === "extract") {
          const f = x.freed_at_weight_ratio;
          tr.append(el("td", "", lab), el("td", f !== null && f <= 2 ? "ok" : "bad", f === null ? "stuck" : `${f.toFixed(2)}× its weight`), el("td", "", `${x.block_lift_mm} mm`));
        } else {
          const o = x.out_of_slot_mm;
          tr.append(el("td", "", lab), el("td", Math.abs(o) <= 2 ? "ok" : "bad", `${o.toFixed(1)} mm`), el("td", "", `${x.rotation_deg}°`));
        }
        k.append(tr);
      }
    }
    overlap(p);
    // stability: how far it can tilt each way before its centre of mass passes the edge of its footprint
    const st = s.stability.tip_angle_deg;
    $("vr-stab").textContent = "";
    for (const [side, a] of Object.entries(st)) {
      const d = el("div", a < 12 ? "bad" : "");
      d.append(el("span", "", side), el("b", "", `${a}°`));
      $("vr-stab").append(d);
    }
    $("vr-load").textContent = s.load_check
      ? `Loads in plain MuJoCo ${s.load_check.mujoco}: ${Object.keys(s.load_check.objects).length} object files and a scene that settles within ${Math.max(...Object.values(s.load_check.scene.drift_mm_after_1s)).toFixed(1)} mm in 1 s.`
      : "";
  }
  draw();
}

---
license: apache-2.0
pretty_name: GenesisWorkbenchEnv AgentEnv episodes
language:
- en
tags:
- agentenv
- mcp
- physics-simulation
- genesis
- sim-ready-assets
- llm-agents
- reinforcement-learning
task_categories:
- other
size_categories:
- n<1K
license_details: "Apache-2.0 for the episodes; rendered frames of Google Scanned Objects and ReplicaCAD assets are CC BY 4.0"
configs:
- config_name: default
  data_files: episodes.jsonl
- config_name: contributor_sessions
  data_files: sessions.jsonl
---

# GenesisWorkbenchEnv AgentEnv episodes

Recorded episodes of an LLM agent fixing the physics of sim-ready objects in
[GenesisWorkbenchEnv](https://github.com/earakely-scale/agentenv-genesis-plugin), an
[agentenv-framework](https://github.com/scaleapi/agentenv-framework) environment built on the
[Genesis](https://github.com/Genesis-Embodied-AI/genesis-world) physics simulator. Browse them in the
[viewer Space](https://huggingface.co/spaces/earakely-scale/GenesisWorkbenchEnv-AgentEnv).

![GenesisWorkbenchEnv: objects as delivered beside the agent's fix, simulated in Genesis](https://huggingface.co/datasets/earakely-scale/GenesisWorkbenchEnv-AgentEnv/resolve/main/showcase.gif)

*Each object as delivered (red) beside the agent's fix (teal), simulated and rendered by Genesis from the MJCF recorded in the episodes: the mug on a tilt table, the oven door unlatched at 5°, the drawer pulled with 10 N. Made with `scripts/make_showcase.py`.*

## What the data is

Each row is one episode. An episode starts from a sim-ready object with a physics defect, and a contributor's
complaint about how it behaves. The complaint is the task prompt as written; nothing tells the agent which
parameter is wrong. The objects are three built from primitives, and eight real assets carrying the defects an
[asset audit](https://github.com/earakely-scale/agentenv-genesis-plugin/blob/main/docs/asset-audit.md) found:
Google Scanned Objects, the VLM-to-CAD rebuilds of them, and ReplicaCAD furniture. The agent edits the object
through MCP tools backed by a live Genesis scene:

| Tool | What it does |
|---|---|
| `inspect` | reports a body's inertial, joints, geoms and materials |
| `render` | simulates a short interaction and returns PNG frames |
| `set_inertial` | sets or recomputes a body's mass, centre of mass and inertia |
| `set_material` | assigns a material, which sets density and so mass |
| `set_joint` | adds a joint or changes its type, axis, range, damping and friction |
| `run_probe` | the agent's own probe, for checking its fix |
| `diff` | the change so far |
| `set_shell`, `set_scale`, `remove_part` | mesh objects: hollow a part to a wall thickness, rescale, drop a stray part |
| `source_photo` | VLM-to-CAD objects: the photos of the real object the CAD was built from, on a 1 cm grid |
| `set_self_collision` | articulated furniture: whether the object's links collide with each other |
| `last_interaction` | contributor sessions: what the contributor did in the browser, what each action showed them, and its image |

After the agent's last turn, a hidden probe suite runs the edited object in Genesis with settings and
tolerances the agent never sees, and checks it against reference behaviour. The checks give a score from 0 to
1 and a pass or fail.

There are eleven tasks. Three are on objects built from primitives:

| Task | Object | Complaint | The bug | Hidden probe |
|---|---|---|---|---|
| `mug-tip` | `mug_017` | "The cup doesn't tip over when I lean it." | The inertial frame sits at the base (COM 0.3 cm), so the mug rights itself from almost any lean. Recomputed from the ceramic shell, the COM is about 3.95 cm up and the mug tips on a tilt table near 45.4°. | tilt table: final tilt for each lean angle |
| `oven-door` | `oven_004` | "Make the oven door heavier so it swings open." | The door is 1.2 kg with 4.0 N·m of hinge friction, so it stays wherever it is put. Heavier and with lower friction, it opens from 5° to its 90° stop. | hinge angle over time after release at 5° |
| `drawer-slide` | `cabinet_022` | "This drawer should slide." | The drawer has no joint. A slide joint with a range lets a 10 N pull open it. | drawer travel over time under a 10 N pull |

Eight are on real assets:

| Task | Object (source) | Complaint | The defect | Hidden checks |
|---|---|---|---|---|
| `scan-mug-heavy` | `scan_mug` (GSO) | "This mug weighs a ton when I lift it, like it's full of sand." | No inertial in the file; loaded at water density, 1.25 kg | mass 0.30–0.45 kg, cavity open, rests upright, stable to 20° |
| `scan-bowl-heavy` | `scan_mixing_bowl` (GSO) | "Lifting this plastic mixing bowl feels like lifting a bag of cement." | Same, 10.5 kg | mass 0.25–0.55 kg, cavity open, rests upright |
| `cad-mug-small` | `cad_mug` (VLM-to-CAD) | "This mug is tiny, like an espresso cup next to my hand. It should be a normal coffee mug." | Built at about 0.7× scale; scaling a solid model up then makes it too heavy | height 8.5–11.5 cm, mass 0.25–0.45 kg, cavity, upright |
| `cad-bowl-small` | `cad_serving_bowl` (VLM-to-CAD) | "This is supposed to be a big serving bowl for the table, but it's the size of a cereal bowl." | Built at 0.5× scale | width 28–36 cm, mass 1.2–3.0 kg, cavity, upright |
| `cad-coffeemaker-heavy` | `cad_coffee_maker` (VLM-to-CAD) | "The coffee maker is as heavy as a microwave. I should be able to lift it with one hand." | Solid housing and carafe, 10.7 kg | mass 2.2–3.6 kg, upright, stable to 20° |
| `cad-frother-ring` | `cad_milk_frother` (VLM-to-CAD) | "There's a blue ring floating next to the milk frother, and it moves with the frother when I push it." | The cord is a separate ring 2 cm away | no floating parts, mass 0.4–1.2 kg, upright |
| `rcad-cabinet-sliders` | `rcad_cabinet` (ReplicaCAD) | "The sliding doors on this cabinet won't slide." | Self-collision between the doors and the frame | every door reaches 90% of its travel, within limits |
| `rcad-cupboard-doors` | `rcad_cupboard` (ReplicaCAD) | "One of the lower doors on the kitchen cupboard won't open, and the one next to it sticks partway." | Self-collision between neighbouring doors | every door reaches 90% of its travel, within limits |

## Results so far

25 episodes: Claude Code with `claude-sonnet-5-5` and `claude-opus-5-5`.

| Tasks | Sonnet 5.5 | Opus 5.5 |
|---|---|---|
| `mug-tip`, `oven-door`, `drawer-slide` | 6 of 6 passed | 3 of 3 passed |
| `scan-mug-heavy`, `scan-bowl-heavy`, `cad-coffeemaker-heavy`, `cad-frother-ring`, `rcad-cabinet-sliders`, `rcad-cupboard-doors` | 6 of 6 passed | 6 of 6 passed |
| `cad-mug-small` | 0.50 | 0.75 |
| `cad-bowl-small` | 0.50 | 0.75 |

The scale tasks are the open problem. Even with `source_photo`, neither model recovered the real size:
- Sonnet left both objects unchanged.
- Opus scaled the mug to 7.4 cm tall (the real one is 9.5 cm) and the bowl to 25 cm across (the real one is 32 cm).

That matches the VLM-to-CAD step itself, which misjudged these same objects' size from these same photos.
Measuring metric size from photos needs a metric source: depth, a known-size reference, or a person.

## With a contributor in the loop

The scale tasks fail because the real size isn't in the asset. The `contributor-*` tasks put a contributor in the loop.
The env serves a page where they look at, lift, lean, drop, fill or shake the object, or open its doors, and each
action runs Genesis. They chat with the agent there, and approve or reject the object. The agent reads what they did
with `last_interaction`. An approval saves the object as a new version of a sim-asset artifact, with its checks and
probe results attached, and the same hidden checks grade the run.

<video src="https://huggingface.co/datasets/earakely-scale/GenesisWorkbenchEnv-AgentEnv/resolve/main/videos/cad_mug.mp4" controls muted playsinline width="100%"></video>

*`contributor-cad-mug`, recorded in Chrome: the contributor lifts the mug (the red cylinder is a 330 ml can, for scale),
says it's tiny, gives its real size when the agent asks, checks the fix, and approves it. Stretches where the agent is
working play at 8× speed. The other recordings: [the milk frother's loose ring](https://huggingface.co/datasets/earakely-scale/GenesisWorkbenchEnv-AgentEnv/resolve/main/videos/cad_milk_frother.mp4)
and [the cupboard's jammed doors](https://huggingface.co/datasets/earakely-scale/GenesisWorkbenchEnv-AgentEnv/resolve/main/videos/rcad_cupboard.mp4).*

A script plays the contributor in these 8 sessions, not a person. It knows each object's real size and weight from the
Google scan the CAD was built from, judges each fix only from what the page shows it, and never sees the hidden checks.
Three sessions went through the page in Chrome with `scripts/record_session.py`, and the rest through the page's HTTP
API with `scripts/contributor.py`. The agent is Claude Code with `claude-sonnet-5-5`.

| Task | Agent alone (Sonnet / Opus) | With a contributor | Contributor | The agent's edits | Saved as |
|---|---|---|---|---|---|
| `contributor-cad-bowl` | 0.50 / 0.75 | 1.00, approved | scripted | `set_scale(factor=1.99)`; `set_shell(part=body, thickness=0.006)`; `set_shell(part=body)`; `set_material(part=body, material=ceramic)`; `set_inertial(link=body, mass=2)` | `cad_serving_bowl` v1 |
| `contributor-cad-coffeemaker` | 1.00 / 1.00 | 1.00, approved | scripted | `set_shell(part=housing, thickness=0.003)`; `set_shell(part=steel, thickness=0.001)`; `set_shell(part=steel_2, thickness=0.001)`; `set_shell(part=housing, thickness=0.002)`; `set_shell(part=steel, thickness=0.0009)`; `set_shell(part=steel_2, thickness=0.0009)` | `cad_coffee_maker` v1 |
| `contributor-cad-frother` | 1.00 / 1.00 | 1.00, approved | browser, recorded | `remove_part(part=cord, restore=False)` | `cad_milk_frother` v1 |
| `contributor-cad-mug` | 0.50 / 0.75 | 1.00, approved | scripted | `set_scale(factor=1.4)`; `set_shell(part=body, thickness=0.004)`; `set_shell(part=body)`; `set_inertial(link=body, mass=0.3)`; `set_scale(factor=1.516)`; `set_inertial(link=body, mass=0.3)` | `cad_mug` v2 |
| `contributor-cad-mug` | 0.50 / 0.75 | 1.00, approved | browser, recorded | `set_scale(factor=1.38)`; `set_inertial(link=body, mass=0.3)`; `set_scale(factor=1.516)`; `set_inertial(link=body, mass=0.3)` | `cad_mug` v3 |
| `contributor-rcad-cupboard` | 1.00 / 1.00 | 1.00, approved | scripted | `set_self_collision(enabled=False)` | `rcad_cupboard` v1 |
| `contributor-rcad-cupboard` | 1.00 / 1.00 | 1.00, approved | browser, recorded | `set_self_collision(enabled=False)` | `rcad_cupboard` v2 |
| `contributor-scan-mug` | 1.00 / 1.00 | 1.00, approved | scripted | `set_material(part=body, material=ceramic)`; `set_shell(part=body, thickness=0.004)` | `scan_mug` v1 |

`sessions.jsonl` (config `contributor_sessions`) has one row per session: the chat, the contributor's actions and what
each showed them, the agent's calls and edits, the checks, and the saved sim asset's id, version and metadata. Each
approval of the same object adds a version, so the mug has several. A pilot mug run is left out: its scripted
contributor gave the agent a rim width that isn't the real mug's.

### Can the browser run MuJoCo instead of Genesis?

A page that simulates in the browser would run MuJoCo's WebAssembly build, with Genesis grading on the server.
[A parity check](https://github.com/earakely-scale/agentenv-genesis-plugin/blob/main/docs/parity.md) ran the probes on the
same MJCF in both engines, for six real assets, each as delivered and edited:

- The tilt-table angle agrees within 5° in all 12 cases, and exactly in 8.
- Drop, rest and cavity agree everywhere.
- Tipping against sliding differs in 3 cases, all at 40–50°, near the 45° where friction 1.0 lets an object slide.

So MuJoCo can carry what the contributor feels, and Genesis stays the grader.

Rendered frames are under `renders/<episode id>/`, and a call's `image` field gives the path of its frame
relative to the repository root.

## Schema

`episodes.jsonl` holds one JSON object per line:

```
{ "id": str, "task": "mug-tip"|"oven-door"|"drawer-slide", "object": str, "complaint": str,
  "model": str, "harness": str, "score": float 0..1, "passed": bool, "duration_s": float, "sample": bool?,
  "checks": [{"id": str, "result": bool, "description": str}],
  "calls": [{"i": int, "tool": str, "args": object, "result": str, "image": str|null}],
  "before": {"mjcf": str, "metrics": object}, "after": {"mjcf": str, "metrics": object}, "diff": str,
  "probe": {"kind": "tip"|"hinge"|"travel", "x": [number], "x_label": str, "y_label": str,
            "before": [number], "after": [number], "reference": [number]|null, "target": str} }
```

| Field | Meaning |
|---|---|
| `id` | episode id, also the folder of its frames under `renders/` |
| `task`, `object`, `complaint` | the task, the object's name and the prompt the agent got |
| `model`, `harness` | the model and the agent harness that ran it |
| `score`, `passed`, `duration_s` | the probe suite's score, its verdict and the episode's wall time |
| `sample` | present and true only on illustrative rows written before real runs; recorded episodes omit it |
| `checks` | each hidden check, its result and what it tests |
| `calls` | every tool call in order: its arguments, its result text and the path of the frame it returned, if any |
| `before`, `after` | the MJCF as given and as the agent left it, with metrics the env computed from each |
| `diff` | a unified diff from `before.mjcf` to `after.mjcf` |
| `probe` | the hidden probe's curve for the object before and after the edit, and for the reference object, with the target it checks |

Two fields are stored differently in `episodes.jsonl` than in the schema: `calls[].args` and
`before.metrics` / `after.metrics` are JSON-encoded strings, because their keys and value types differ from
task to task and the `datasets` library needs one type per column. Decode them with `json.loads`. The Space's
`episodes.json` keeps them as objects.

```python
import json
from datasets import load_dataset

ds = load_dataset("earakely-scale/GenesisWorkbenchEnv-AgentEnv", split="train")
ep = ds[0]
print(ep["task"], ep["score"], ep["passed"])
for call in ep["calls"]:
    print(call["i"], call["tool"], json.loads(call["args"]))
```

## How it was produced

Every episode is a run of the `genesis-workbench` bundle that the plugin ships, on agentenv-framework. Genesis
(genesis-world 1.4.5) ran on its CPU backend on an x86 VM. The agent was Claude Code running
claude-sonnet-5-5, which reached the environment's tools over MCP. agentenv recorded each run; its episode,
frames and probe curves were written under `results/` in the plugin's repository, and
`scripts/publish_hub.py` published them here and to the Space, refusing any file that matched its leak guard.

## How to reproduce

Install agentenv-framework, add the plugin from GitHub, and run the bundle:

```bash
agent-env plugin add 'agentenv-genesis @ git+https://github.com/earakely-scale/agentenv-genesis-plugin'
agent-env run genesis-workbench
```

Claude Code needs a model endpoint that serves Anthropic's Messages API, set in the `[model]` section of
`.agentenv/config.toml`. No GPU is needed.

## Limitations

- This is a prototype: eleven tasks, one object each, and one or two defects per object. Each task has one to three
  recorded runs per model, too few to compare models with confidence.
- Three objects are built from MJCF primitives. The eight real assets are a mug and a bowl from Google Scanned
  Objects, four VLM-to-CAD rebuilds of scanned objects (made by Claude Sonnet 5.5 from two renders each), and two
  ReplicaCAD pieces of furniture.
- The weight bands are our estimates from typical retail specs. The hidden size bands come from the scanned
  originals. Probe tolerances are example values.
- The importer fixes some delivered defects before a task starts and leaves others in:
  - It fixes ReplicaCAD's comma-separated numbers and up-axis, and adds free joints to the scans.
  - It leaves in the defects the tasks are about.
- Genesis ran on its CPU backend. Results on a GPU backend, or under another simulator, may differ within the
  tolerances.
- Passing a task's hidden checks means the object behaves like the reference in those probes, not that every
  physical property is right.

## License

Apache-2.0, the same as Genesis. The dataset, the plugin and the viewer are released under Apache-2.0.

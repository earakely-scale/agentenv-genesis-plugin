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
configs:
- config_name: default
  data_files: episodes.jsonl
---

# GenesisWorkbenchEnv AgentEnv episodes

Recorded episodes of an LLM agent fixing the physics of sim-ready objects in
[GenesisWorkbenchEnv](https://github.com/earakely-scale/agentenv-genesis-plugin), an
[agentenv-framework](https://github.com/scaleapi/agentenv-framework) environment built on the
[Genesis](https://github.com/Genesis-Embodied-AI/genesis-world) physics simulator. Browse them in the
[viewer Space](https://huggingface.co/spaces/earakely-scale/GenesisWorkbenchEnv-AgentEnv).

## What the data is

Each row is one episode. An episode starts from an object, written as MJCF, that a VLM-to-CAD step produced
with a physics bug, and a contributor's complaint about how it behaves. The complaint is the task prompt as
written; nothing tells the agent which parameter is wrong. The agent edits the object through seven MCP tools
backed by a live Genesis scene:

| Tool | What it does |
|---|---|
| `inspect` | reports a body's inertial, joints, geoms and materials |
| `render` | simulates a short interaction and returns PNG frames |
| `set_inertial` | sets or recomputes a body's mass, centre of mass and inertia |
| `set_material` | assigns a material, which sets density and so mass |
| `set_joint` | adds a joint or changes its type, axis, range, damping and friction |
| `run_probe` | the agent's own probe, for checking its fix |
| `diff` | the MJCF change so far |

After the agent's last turn, a hidden probe suite runs the edited object in Genesis with settings and
tolerances the agent never sees, and checks it against reference behaviour. The checks give a score from 0 to
1 and a pass or fail.

There are three tasks:

| Task | Object | Complaint | The bug | Hidden probe |
|---|---|---|---|---|
| `mug-tip` | `mug_017` | "The cup doesn't tip over when I lean it." | The inertial frame sits at the base (COM 0.3 cm), so the mug rights itself from almost any lean. Recomputed from the ceramic shell, the COM is about 3.95 cm up and the mug tips on a tilt table near 45.4°. | tilt table: final tilt for each lean angle |
| `oven-door` | `oven_004` | "Make the oven door heavier so it swings open." | The door is 1.2 kg with 4.0 N·m of hinge friction, so it stays wherever it is put. Heavier and with lower friction, it opens from 5° to its 90° stop. | hinge angle over time after release at 5° |
| `drawer-slide` | `cabinet_022` | "This drawer should slide." | The drawer has no joint. A slide joint with a range lets a 10 N pull open it. | drawer travel over time under a 10 N pull |

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

- This is a prototype. There are three tasks, one object each, and one bug per object.
- The example objects are built from MJCF primitives (boxes, cylinders and capsules), not from meshes a
  VLM-to-CAD step produced.
- The probe tolerances, reference parameters and material densities are example values, chosen to make the
  three bugs and their fixes distinct, not measured from real objects.
- Genesis ran on its CPU backend. Results on a GPU backend, or under another simulator, may differ within the
  tolerances.
- Each task has a small set of hidden checks. Passing them means the object behaves like the reference in that
  probe, not that every physical property is right.

## License

Apache-2.0, the same as Genesis. The dataset, the plugin and the viewer are released under Apache-2.0.

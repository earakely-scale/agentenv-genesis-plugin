# GenesisWorkbenchEnv for AgentEnv

An [AgentEnv](https://github.com/scaleapi/agentenv-framework) environment where an agent fixes the physics of
sim-ready objects, measured by the [Genesis](https://github.com/Genesis-Embodied-AI/genesis-world) physics simulator.

Objects come out of a VLM-to-CAD pipeline as MJCF, and they're often physically wrong. A mug that rights itself like a
weighted toy, an oven door too light to swing open, a drawer welded shut. In this env a contributor's complaint is the
task. The agent inspects the object, renders it and runs Genesis probes, then edits mass properties, materials and
joints through MCP tools. A hidden probe suite in Genesis grades the result against how the real object behaves.

- Dataset of recorded episodes: [earakely-scale/GenesisWorkbenchEnv-AgentEnv](https://huggingface.co/datasets/earakely-scale/GenesisWorkbenchEnv-AgentEnv)
- Episode viewer: [Space](https://huggingface.co/spaces/earakely-scale/GenesisWorkbenchEnv-AgentEnv)

## Tasks

| Task | Object | Complaint | What is actually wrong | Hidden checks |
|---|---|---|---|---|
| `mug-tip` | `mug_017` | "The cup doesn't tip over when I lean it." | The inertial frame sits at the base (COM 0.3 cm), so it can lean 85° without falling | tips on a tilt table within 6° of a real mug (45.4°); ceramic mass band; rests upright |
| `oven-door` | `oven_004` | "Make the oven door heavier so it swings open." | 1.2 kg door against 4.0 N·m of hinge friction; gravity supplies at most 2.6 N·m | released at 5°, opens to ≥ 85° within 2 s; stops at its 90° limit; mass in a steel-and-glass door's band |
| `drawer-slide` | `cabinet_022` | "This drawer should slide." | The drawer has no joint | a 10 N pull opens it ≥ 0.30 m; it stops before leaving the cabinet; a slide joint along the depth axis |
| `wiring` | `mug_017` | none | No agent: loads and grades the object as delivered | expected to fail |

## Tools the agent gets

| Tool | What it does |
|---|---|
| `inspect()` | Links with mass, centre of mass, inertia and material; joints; the materials table |
| `render()` | Schematic side view with the centre of mass and joints, beside the last probe's curve (PNG) |
| `set_inertial(link, mass?, com?, inertia?, from_material?)` | Set mass properties, or recompute them from the link's geometry at a material's density |
| `set_material(link, material)` | Real material: friction plus recomputed mass properties |
| `set_joint(name, type?, axis?, range?, damping?, frictionloss?)` | Create or edit a hinge or slide joint |
| `run_probe(kind)` | Build a Genesis scene from the current MJCF and measure `tip`, `rest`, `hinge` or `travel`; numbers plus a plot |
| `diff()` | MJCF diff against the object as delivered |

The tilt-table probe rotates gravity per environment in one batched `scene.build(n_envs=12)`. That's a quasi-static
tilt test, and it avoids the impulse Genesis applies when an object's pose is set directly into a leaning contact.

## Run it

```bash
uv tool install agentenv-framework
agent-env plugin add 'agentenv-genesis @ git+https://github.com/earakely-scale/agentenv-genesis-plugin'
cp .agentenv/config.example.toml .agentenv/config.toml   # set [model] base_url; export LITELLM_API_KEY
agent-env run genesis-workbench
```

The env image installs `genesis-world==1.4.5` with CPU PyTorch and runs Genesis on its CPU backend, so any Docker
host works. The first probe in a container compiles Genesis kernels (about 25 s); later probes build in well under a
second. Set `GENESIS_EPISODES_DIR` to keep each graded episode as JSON: every tool call, the renders, the MJCF before
and after, and the probe curves. `scripts/publish_hub.py` turns those into the dataset and the Space.

## Layout

- `src/agentenv_genesis/bundles/genesis-workbench/`: the bundle
  - `envs/genesis-workbench/`: the env image (`server.py` is the MCP server; `workbench.py` has the objects, MJCF, probes and renders)
  - `tasks/`: one JSON per task
  - `artifacts/workbench-probes/verify.py`: the verifier, which reads the hidden probe suite through `data/get`
- `hub/`: the Hugging Face Space and dataset card
- `scripts/publish_hub.py`: publish episodes to the Hub

## Limitations

This is a prototype. The three objects are built from primitives rather than CAD meshes, the renders are schematics drawn
from simulator state, and the probe tolerances and material bands are example values. Genesis's default contact model
is not identical to MuJoCo's, so the same MJCF can behave differently in other engines.

## License

Apache-2.0. Genesis is Apache-2.0.

# GenesisWorkbenchEnv for AgentEnv

![The mug, oven door and drawer as delivered beside the agent's fix, simulated in Genesis](hub/showcase.gif)

*Each object as delivered (red) beside the agent's fix (teal), simulated and rendered by Genesis from the MJCF recorded in the episodes: the mug on a tilt table, the oven door unlatched at 5°, the drawer pulled with 10 N.*

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

Eight more tasks use real assets and the defects [the asset audit](docs/asset-audit.md) found in them:

| Task | Object (source) | Complaint | What is actually wrong | Hidden checks |
|---|---|---|---|---|
| `scan-mug-heavy` | `scan_mug` (Google Scanned Objects) | "This mug weighs a ton when I lift it, like it's full of sand." | No inertial in the file, so the loader fills it at water density: 1.25 kg | mass 0.30–0.45 kg; cavity open; rests upright; stable to 20° |
| `scan-bowl-heavy` | `scan_mixing_bowl` (GSO) | "Lifting this plastic mixing bowl feels like lifting a bag of cement." | Same, 10.5 kg for a plastic bowl | mass 0.25–0.55 kg; cavity open; rests upright |
| `cad-mug-small` | `cad_mug` (VLM-to-CAD) | "This mug is tiny, like an espresso cup next to my hand…" | The VLM built it at 0.7× scale; scaling it up then makes it too heavy | height 8.5–11.5 cm; mass 0.25–0.45 kg; cavity; upright |
| `cad-bowl-small` | `cad_serving_bowl` (VLM-to-CAD) | "This is supposed to be a big serving bowl for the table, but it's the size of a cereal bowl." | Built at 0.5× scale | width 28–36 cm; mass 1.2–3.0 kg; cavity; upright |
| `cad-coffeemaker-heavy` | `cad_coffee_maker` (VLM-to-CAD) | "The coffee maker is as heavy as a microwave…" | Solid plastic housing and a solid steel carafe: 10.7 kg | mass 2.2–3.6 kg; upright; stable to 20° |
| `cad-frother-ring` | `cad_milk_frother` (VLM-to-CAD) | "There's a blue ring floating next to the milk frother…" | The power cord is a separate ring 2 cm from the body | no floating parts; mass 0.4–1.2 kg; upright |
| `rcad-cabinet-sliders` | `rcad_cabinet` (ReplicaCAD) | "The sliding doors on this cabinet won't slide." | The doors' collision boxes collide with each other and the frame (self-collision) | every door reaches 90% of its travel, within limits |
| `rcad-cupboard-doors` | `rcad_cupboard` (ReplicaCAD) | "One of the lower doors on the kitchen cupboard won't open…" | Same, between neighbouring doors | every door reaches 90% of its travel, within limits |

## Tools the agent gets

| Tool | What it does |
|---|---|
| `inspect()` | Links with mass, centre of mass, inertia and material; joints; the materials table |
| `render()` | Schematic side view with the centre of mass and joints, beside the last probe's curve (PNG) |
| `set_inertial(link, mass?, com?, inertia?, from_material?)` | Set mass properties, or recompute them from the link's geometry at a material's density |
| `set_material(link, material)` | Real material: friction plus recomputed mass properties |
| `set_joint(name, type?, axis?, range?, damping?, frictionloss?)` | Create or edit a hinge or slide joint |
| `run_probe(kind)` | Build a Genesis scene from the current MJCF and measure `tip`, `rest`, `hinge` or `travel`; numbers plus a plot |
| `diff()` | What changed against the object as delivered |
| `set_shell(part, thickness)` | Mesh objects: make a part a hollow shell, its mass from surface area × thickness × density |
| `set_scale(factor)` | Mesh objects: rescale uniformly; mass properties follow the geometry |
| `source_photo()` | VLM-to-CAD objects: the photos of the real object the CAD was built from, on a 1 cm grid |
| `remove_part(part, restore?)` | Mesh objects: drop a stray part, or put it back |
| `set_self_collision(enabled)` | Articulated objects: whether the object's links collide with each other |

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

Apache-2.0. Genesis is Apache-2.0. The scanned and ReplicaCAD assets are CC BY 4.0; see NOTICE.

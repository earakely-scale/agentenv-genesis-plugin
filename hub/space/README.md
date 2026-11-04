---
title: GenesisWorkbenchEnv AgentEnv
emoji: 🧪
colorFrom: green
colorTo: blue
sdk: static
app_file: index.html
pinned: false
license: apache-2.0
short_description: Agents fix sim-ready object physics in Genesis
---

# GenesisWorkbenchEnv AgentEnv

The episode viewer for GenesisWorkbenchEnv, an [agentenv-framework](https://github.com/scaleapi/agentenv-framework)
environment built on the [Genesis](https://github.com/Genesis-Embodied-AI/genesis-world) physics simulator
(genesis-world 1.4.5).

Each task starts from a sim-ready object (MJCF) that a VLM-to-CAD step produced with a physics bug, and a
contributor's complaint about it, which is the whole task prompt. An LLM agent (Claude Code running
claude-sonnet-5-5) fixes the object through MCP tools: `inspect`, `render` (returns PNG frames), `set_inertial`,
`set_material`, `set_joint`, `run_probe` and `diff`. After the agent's last turn, a hidden probe suite runs the
edited object in Genesis and compares its behaviour with a reference, for a score from 0 to 1 and a pass or fail.

| Task | Complaint | Hidden probe |
|---|---|---|
| `mug-tip` | "The cup doesn't tip over when I lean it." | tilt table: final tilt for each lean angle |
| `oven-door` | "Make the oven door heavier so it swings open." | hinge angle over time after release at 5° |
| `drawer-slide` | "This drawer should slide." | drawer travel over time under a 10 N pull |

The page reads `episodes.json` and the frames under `renders/<episode id>/`. Until the first recorded runs are
published it shows three sample episodes, marked as samples.

- Code: https://github.com/earakely-scale/agentenv-genesis-plugin
- Data: https://huggingface.co/datasets/earakely-scale/GenesisWorkbenchEnv-AgentEnv

Apache-2.0.

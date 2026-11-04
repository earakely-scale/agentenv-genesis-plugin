# Do Genesis and MuJoCo agree on what a contributor sees?

The contributor page runs every action in Genesis on the server. A page that simulates in the browser would run MuJoCo
(its WebAssembly build) instead, with Genesis grading on the server. That split only works if the two engines agree on
what a contributor sees and on what the hidden checks measure. `scripts/parity_mujoco.py` tests this. It runs the
workbench's probes on one MJCF file per object in Genesis 1.4.5 and in MuJoCo 3.17.0, with the same timestep (2 ms),
floor friction (1.0), gravity ramp and thresholds. It covers six real mesh assets, each as delivered and with a
realistic fix applied through the env's own edit tools.

## Results

| Object | Variant | Mass | Tilt table: first failure, Genesis / MuJoCo | Drop: settles (s) | Drop: final tilt | Rests upright | Marbles inside |
|---|---|---|---|---|---|---|---|
| scan_mug | delivered | 1.25 kg | 25° topple / 25° topple | 0.35 / 0.40 | 0.6° / 0.6° | yes / yes | 5 / 5 |
| scan_mug | edited | 0.34 kg | 25° topple / 25° topple | 0.35 / 0.40 | 0.6° / 0.6° | yes / yes | 5 / 5 |
| scan_mixing_bowl | delivered | 10.5 kg | 45° / 45° | 0.40 / 0.40 | 0.8° / 0.9° | yes / yes | 5 / 5 |
| scan_mixing_bowl | edited | 0.25 kg | 45° / 45° | 0.35 / 0.40 | 0.8° / 0.9° | yes / yes | 5 / 5 |
| cad_mug | delivered | 0.20 kg | 45° topple / 45° **slide** | 0.25 / 0.30 | 0° / 0° | yes / yes | 5 / 5 |
| cad_mug | edited | 0.35 kg | 50° topple / 45° **slide** | 0.25 / 0.35 | 0° / 0° | yes / yes | 5 / 5 |
| cad_serving_bowl | delivered | 0.42 kg | 45° / 45° | 0.25 / 0.30 | 0° / 0° | yes / yes | 5 / 5 |
| cad_serving_bowl | edited | 1.90 kg | 45° / 50° | 0.35 / 0.40 | 0° / 0° | yes / yes | 5 / 5 |
| cad_coffee_maker | delivered | 10.7 kg | 40° topple / 40° **slide** | 0.50 / 0.55 | 0° / 0° | yes / yes | n/a |
| cad_coffee_maker | edited | 4.31 kg | 40° / 40° | 0.50 / 0.55 | 0° / 0° | yes / yes | n/a |
| cad_milk_frother | delivered | 0.67 kg | 40° / 35° | 0.35 / 0.40 | 0° / 0° | yes / yes | n/a |
| cad_milk_frother | edited | 0.66 kg | 35° / 40° | 0.30 / 0.40 | 0° / 0° | yes / yes | n/a |

Raw readings, including both tilt curves per object: `docs/parity/parity.json`.

- **The tilt-table angle agrees within one 5° step on all 12 cases, and exactly on 8.** It's the reading the
  stability checks (`tilt_min_deg`) grade, and the "Lean" action shows it.
- **How the object fails differs on 3 of 12.** Genesis has the mug and the coffee maker tip over, and MuJoCo has them
  slide off, at the same angle. All three fail at 40–50°. With friction 1.0, an object starts to slide at 45°
  (tan 45° = 1), so tipping and sliding are within a few degrees of each other there. The two engines resolve that
  close call differently. A contributor would see the difference ("it tips over" against "it slides off"), but no
  hidden check depends on which one happens.
- **Drop, rest and cavity agree everywhere.** The objects end upright in both engines, rest stably, and keep all five
  marbles. MuJoCo reports settling 0.05–0.1 s later. That reflects its contacts damping a little more slowly, and is
  well under what a person would notice.
- **MuJoCo was about 10× faster here:** 2.1 s against 21.8 s for all 24 probe runs, both on CPU. Genesis's speed
  comes from batching thousands of environments on a GPU, which a single interactive object doesn't use.

## What this means for the browser

The browser can run MuJoCo for what the contributor feels, and Genesis can stay the grader on the server, for rigid
objects like these. Two cautions:

1. Grade with Genesis, not with what the browser showed. Near the friction limit the two engines can disagree on
   tipping against sliding, so a contributor's "it slides" may not reproduce in Genesis exactly.
2. This covers rigid objects only. Articulated furniture depends on self-collision filtering, which the asset audit
   showed differs between simulators (ReplicaCAD's doors jam with Genesis's default and move freely in Habitat). Check
   it separately before moving doors and drawers into the browser.

## Reproduce

```bash
python scripts/parity_mujoco.py --out docs/parity   # needs genesis-world 1.4.5, mujoco, trimesh, scipy
```

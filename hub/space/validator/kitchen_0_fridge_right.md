# kitchen_0_fridge_right: warn

`kitchen_0_fridge_right` (scene.xml), MuJoCo 3.17.0, 23 bodies, 240.8858 kg, timestep 1 ms

| | Check | What it found |
|---|---|---|
| ✓ | loads | loads in MuJoCo 3.17.0: 23 bodies, 263 geoms, 19 joints |
| ✓ | mass-source | every colliding body has an explicit <inertial> |
| ✓ | density | mass over the volume MuJoCo collides (convex hulls, so a hollow object reads low): counter/drawer1_top 81 kg/m³ (5.0 kg), counter/drawer1_bottom 81 kg/m³ (5.0 kg), counter/drawer3 130 kg/m³ (4.0 kg), counter/drawer4 141 kg/m³ (6.0 kg), counter/drawer2_top 159 kg/m³ (4.0 kg), counter/drawer2_middle 159 kg/m³ (4.0 kg) |
| ⚠ | inertia | rotational inertia against a solid of the same mass filling its collision hull (1 = the same): counter/body 0.01×, fridge/body 0.06×, counter/drawer2_top 5.83×, counter/drawer2_middle 5.83×, counter/drawer2_bottom 5.83×, counter/drawer3 3.77× |
| ⚠ | centre-of-mass | centre of mass outside the body: fridge/top_door at [0.0, 0.0, 0.0], fridge/bottom_door at [0.0, 0.0, 0.0] |
| ✓ | friction | 59 of 226 colliding geoms have MuJoCo's default friction (1.0 sliding): the rest are set |
| ✓ | contact-softness | 0 of 226 colliding geoms use MuJoCo's default contact softness (solref 0.02 s) |
| ✓ | hull-fill | no colliding mesh loses much to its convex hull |
| ✓ | floating-pieces | every body's collision pieces touch each other |
| ⚠ | visual-fit | these visual meshes reach beyond their collision geometry: what's drawn there doesn't collide, and what rests on it looks sunk in: counter/body by 361.9 mm, fridge/top_door by 162.3 mm, fridge/body by 61.8 mm, fridge/bottom_door by 52.7 mm, counter/drawer2_top by 46.1 mm, counter/drawer2_middle by 46.1 mm |
| ✓ | welded-bodies | 2 child bodies have no joint and move with their parent: counter/body, fridge/body |
| ✓ | joints | 9 hinge or slide joints: counter/left_slide_top (slide, range [0.0, 0.5], damping 0.01), counter/left_slide_bottom (slide, range [0.0, 0.5], damping 0.01), counter/middle_slide_top (slide, range [0.0, 0.5], damping 0.01), counter/middle_slide_middle (slide, range [0.0, 0.5], damping 0.01), counter/middle_slide_bottom (slide, range [0.0, 0.5], damping 0.01), counter/right_slide_top (slide, range [0.0, 0.5], damping 0.01), counter/right_slide_bottom (slide, range [0.0, 0.5], damping 0.01), fridge/top_door_hinge (hinge, range [0.0, 2.3], damping 0.01) |
| ✓ | fixed-to-world | its root is fixed to the world, as furniture is (kept with --keep-fixed) |
| ✓ | starts-apart | no body starts inside another |
| ⚠ | settles | after 2 s on the floor nothing moves more than 0.3 mm or 0.3° in 0.2 s; knife_block_chef_20cm/chef_20cm shakes in place at 0.50 (m/s or rad/s) without going anywhere |
| ✓ | no-overlap | settling: deepest at any moment knife_block_boning_15cm/boning_15cm 1.54 mm into knife_block_knife_block/knife_block at 0.52 s; once settled, between bodies none, into the floor none (allowed: 0.5 mm between bodies and 1 mm into the floor at rest, 3 mm at any moment) |
| ✓ | timestep | settled at 1 ms and at 0.5 ms steps, knife_block_chef_20cm/chef_20cm ends 2.9 mm and 1.7° apart |
| ✓ | joints-hold | left alone for 2 s, none of the 9 joints moves more than 5° or 5 mm |
| ✓ | joints-reach | each of the 9 limited joints reaches both ends of its range when driven |

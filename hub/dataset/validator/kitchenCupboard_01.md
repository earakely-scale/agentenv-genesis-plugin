# kitchenCupboard_01: fail

`kitchenCupboard_01.xml` (kitchenCupboard_01.xml), MuJoCo 3.17.0, 10 bodies, 134.0 kg, timestep 2 ms

| | Check | What it found |
|---|---|---|
| ✓ | loads | loads in MuJoCo 3.17.0: 10 bodies, 36 geoms, 8 joints |
| ✓ | mass-source | every colliding body has an explicit <inertial> |
| ⚠ | density | mass over the volume MuJoCo collides (convex hulls, so a hollow object reads low): body 15 kg/m³ (70.0 kg), kitchencupboard_doorWhole_1L 82 kg/m³ (8.0 kg), kitchencupboard_doorWhole_2L 82 kg/m³ (8.0 kg), kitchencupboard_doorWindow_1L 82 kg/m³ (8.0 kg), kitchencupboard_doorWindow_1R 82 kg/m³ (8.0 kg), kitchencupboard_doorWindow_2R 82 kg/m³ (8.0 kg); implausible: body |
| ⚠ | inertia | rotational inertia against a solid of the same mass filling its collision hull (1 = the same): body 0.0×, kitchencupboard_doorWhole_1L 0.45×, kitchencupboard_doorWhole_1R 0.45×, kitchencupboard_doorWhole_2L 0.45×, kitchencupboard_doorWhole_2R 0.45×, kitchencupboard_doorWindow_1L 0.45× |
| ⚠ | centre-of-mass | centre of mass outside the body: kitchencupboard_doorWindow_1L at [0.0, 0.0, 0.0], kitchencupboard_doorWindow_1R at [0.0, 0.0, 0.0], kitchencupboard_doorWindow_2L at [0.0, 0.0, 0.0], kitchencupboard_doorWindow_2R at [0.0, 0.0, 0.0] |
| ⚠ | friction | 26 of 26 colliding geoms have MuJoCo's default friction (1.0 sliding): none is set from a material |
| ✓ | contact-softness | 26 of 26 colliding geoms use MuJoCo's default contact softness (solref 0.02 s) |
| ✓ | hull-fill | no colliding mesh loses much to its convex hull |
| ✓ | floating-pieces | every body's collision pieces touch each other |
| ⚠ | visual-fit | these visual meshes reach beyond their collision geometry: what's drawn there doesn't collide, and what rests on it looks sunk in: kitchencupboard_doorWindow_1L by 182.9 mm, kitchencupboard_doorWindow_2L by 161.1 mm, kitchencupboard_doorWindow_1R by 149.5 mm, kitchencupboard_doorWindow_2R by 149.5 mm, kitchencupboard_doorWhole_1L by 116.2 mm, kitchencupboard_doorWhole_1R by 116.2 mm |
| ✓ | welded-bodies | 1 child bodies have no joint and move with their parent: body |
| ✓ | joints | 8 hinge or slide joints: doorWhole_1L_hinge (hinge, range [-2.3, 0.0], damping 0.01), doorWhole_1R_hinge (hinge, range [0.0, 2.3], damping 0.01), doorWhole_2L_hinge (hinge, range [-2.3, 0.0], damping 0.01), doorWhole_2R_hinge (hinge, range [0.0, 2.3], damping 0.01), doorWindow_1L_hinge (hinge, range [-2.3, 0.0], damping 0.01), doorWindow_1R_hinge (hinge, range [0.0, 2.3], damping 0.01), doorWindow_2L_hinge (hinge, range [-2.3, 0.0], damping 0.01), doorWindow_2R_hinge (hinge, range [0.0, 2.3], damping 0.01) |
| ✓ | fixed-to-world | its root is fixed to the world, as furniture is (kept with --keep-fixed) |
| ✓ | starts-apart | no body starts inside another |
| ✓ | settles | after 2 s on the floor nothing moves more than 0.0 mm or 0.0° in 0.2 s |
| ✓ | no-overlap | settling: deepest at any moment none; once settled, between bodies none, into the floor none (allowed: 0.5 mm between bodies and 1 mm into the floor at rest, 3 mm at any moment) |
| ✓ | joints-hold | left alone for 2 s, none of the 8 joints moves more than 5° or 5 mm |
| ✗ | joints-reach | driven to the ends of their range, these stop short: doorWhole_1R_hinge upper end 68% (kitchencupboard_doorWhole_1R 0.16 mm into kitchencupboard_doorWhole_2L), doorWhole_2L_hinge lower end 68% (kitchencupboard_doorWhole_2L 0.17 mm into kitchencupboard_doorWhole_1R) |

# kitchen_counter: warn

`kitchen_counter.xml` (kitchen_counter.xml), MuJoCo 3.17.0, 9 bodies, 152.0 kg, timestep 2 ms

| | Check | What it found |
|---|---|---|
| ✓ | loads | loads in MuJoCo 3.17.0: 9 bodies, 56 geoms, 7 joints |
| ✓ | mass-source | every colliding body has an explicit <inertial> |
| ✓ | density | mass over the volume MuJoCo collides (convex hulls, so a hollow object reads low): drawer1_top 81 kg/m³ (5.0 kg), drawer1_bottom 81 kg/m³ (5.0 kg), drawer3 130 kg/m³ (4.0 kg), drawer4 141 kg/m³ (6.0 kg), drawer2_top 159 kg/m³ (4.0 kg), drawer2_middle 159 kg/m³ (4.0 kg) |
| ⚠ | inertia | rotational inertia against a solid of the same mass filling its collision hull (1 = the same): body 0.01×, drawer2_top 5.83×, drawer2_middle 5.83×, drawer2_bottom 5.83×, drawer3 3.77×, drawer1_top 2.8× |
| ✓ | centre-of-mass | every body's centre of mass is within its collision geometry's bounds |
| ⚠ | friction | 47 of 47 colliding geoms have MuJoCo's default friction (1.0 sliding): none is set from a material |
| ✓ | contact-softness | 47 of 47 colliding geoms use MuJoCo's default contact softness (solref 0.02 s) |
| ✓ | hull-fill | no colliding mesh loses much to its convex hull |
| ✓ | floating-pieces | every body's collision pieces touch each other |
| ⚠ | visual-fit | these visual meshes reach beyond their collision geometry: what's drawn there doesn't collide, and what rests on it looks sunk in: body by 361.9 mm, drawer2_top by 46.1 mm, drawer2_middle by 46.1 mm, drawer2_bottom by 46.1 mm, drawer4 by 45.9 mm, drawer1_top by 35.1 mm |
| ✓ | welded-bodies | 1 child bodies have no joint and move with their parent: body |
| ✓ | joints | 7 hinge or slide joints: left_slide_top (slide, range [0.0, 0.5], damping 0.01), left_slide_bottom (slide, range [0.0, 0.5], damping 0.01), middle_slide_top (slide, range [0.0, 0.5], damping 0.01), middle_slide_middle (slide, range [0.0, 0.5], damping 0.01), middle_slide_bottom (slide, range [0.0, 0.5], damping 0.01), right_slide_top (slide, range [0.0, 0.5], damping 0.01), right_slide_bottom (slide, range [0.0, 0.5], damping 0.01) |
| ✓ | fixed-to-world | its root is fixed to the world, as furniture is (kept with --keep-fixed) |
| ✓ | starts-apart | no body starts inside another |
| ✓ | settles | after 2 s on the floor nothing moves more than 0.0 mm or 0.0° in 0.2 s |
| ✓ | no-overlap | settling: deepest at any moment none; once settled, between bodies none, into the floor none (allowed: 0.5 mm between bodies and 1 mm into the floor at rest, 3 mm at any moment) |
| ✓ | joints-hold | left alone for 2 s, none of the 7 joints moves more than 5° or 5 mm |
| ✓ | joints-reach | each of the 7 limited joints reaches both ends of its range when driven |

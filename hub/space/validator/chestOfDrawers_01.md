# chestOfDrawers_01: warn

`chestOfDrawers_01.xml` (chestOfDrawers_01.xml), MuJoCo 3.17.0, 8 bodies, 118.0 kg, timestep 2 ms

| | Check | What it found |
|---|---|---|
| ✓ | loads | loads in MuJoCo 3.17.0: 8 bodies, 55 geoms, 6 joints |
| ✓ | mass-source | every colliding body has an explicit <inertial> |
| ✓ | density | mass over the volume MuJoCo collides (convex hulls, so a hollow object reads low): drawer_botL 53 kg/m³ (8.0 kg), drawer_botR 53 kg/m³ (8.0 kg), drawer_midL 57 kg/m³ (8.0 kg), drawer_midR 57 kg/m³ (8.0 kg), body 70 kg/m³ (70.0 kg), drawer_topL 133 kg/m³ (8.0 kg) |
| ⚠ | inertia | rotational inertia against a solid of the same mass filling its collision hull (1 = the same): body 0.02×, drawer_topL 1.43×, drawer_topR 1.43×, drawer_botL 0.8×, drawer_botR 0.8×, drawer_midL 0.91× |
| ⚠ | centre-of-mass | centre of mass outside the body: drawer_topL at [0.0, -0.1, 0.0], drawer_topR at [0.0, -0.1, 0.0], drawer_midL at [0.0, -0.1, 0.0], drawer_midR at [0.0, -0.1, 0.0] |
| ⚠ | friction | 47 of 47 colliding geoms have MuJoCo's default friction (1.0 sliding): none is set from a material |
| ✓ | contact-softness | 47 of 47 colliding geoms use MuJoCo's default contact softness (solref 0.02 s) |
| ✓ | hull-fill | no colliding mesh loses much to its convex hull |
| ✓ | floating-pieces | every body's collision pieces touch each other |
| ⚠ | visual-fit | these visual meshes reach beyond their collision geometry: what's drawn there doesn't collide, and what rests on it looks sunk in: body by 595.9 mm, drawer_midL by 38.0 mm, drawer_midR by 38.0 mm, drawer_topL by 36.5 mm, drawer_topR by 36.5 mm, drawer_botL by 33.3 mm |
| ✓ | welded-bodies | 1 child bodies have no joint and move with their parent: body |
| ✓ | joints | 6 hinge or slide joints: drawer_topL (slide, range [0.0, 0.5], damping 0.01), drawer_topR (slide, range [0.0, 0.5], damping 0.01), drawer_midL (slide, range [0.0, 0.5], damping 0.01), drawer_midR (slide, range [0.0, 0.5], damping 0.01), drawer_botL (slide, range [0.0, 0.5], damping 0.01), drawer_botR (slide, range [0.0, 0.5], damping 0.01) |
| ✓ | fixed-to-world | its root is fixed to the world, as furniture is (kept with --keep-fixed) |
| ✓ | starts-apart | no body starts inside another |
| ✓ | settles | after 2 s on the floor nothing moves more than 0.0 mm or 0.0° in 0.2 s |
| ✓ | no-overlap | settling: deepest at any moment none; once settled, between bodies none, into the floor none (allowed: 0.5 mm between bodies and 1 mm into the floor at rest, 3 mm at any moment) |
| ✓ | joints-hold | left alone for 2 s, none of the 6 joints moves more than 5° or 5 mm |
| ✓ | joints-reach | each of the 6 limited joints reaches both ends of its range when driven |

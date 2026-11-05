# fridge: warn

`fridge.xml` (fridge.xml), MuJoCo 3.17.0, 4 bodies, 84.0 kg, timestep 2 ms

| | Check | What it found |
|---|---|---|
| ✓ | loads | loads in MuJoCo 3.17.0: 4 bodies, 16 geoms, 2 joints |
| ✓ | mass-source | every colliding body has an explicit <inertial> |
| ✓ | density | mass over the volume MuJoCo collides (convex hulls, so a hollow object reads low): top_door 191 kg/m³ (8.0 kg), bottom_door 207 kg/m³ (6.0 kg), body 428 kg/m³ (70.0 kg) |
| ⚠ | inertia | rotational inertia against a solid of the same mass filling its collision hull (1 = the same): body 0.06×, bottom_door 2.82×, top_door 1.33× |
| ⚠ | centre-of-mass | centre of mass outside the body: top_door at [0.0, 0.0, 0.0], bottom_door at [0.0, 0.0, 0.0] |
| ⚠ | friction | 12 of 12 colliding geoms have MuJoCo's default friction (1.0 sliding): none is set from a material |
| ✓ | contact-softness | 12 of 12 colliding geoms use MuJoCo's default contact softness (solref 0.02 s) |
| ✓ | hull-fill | no colliding mesh loses much to its convex hull |
| ✓ | floating-pieces | every body's collision pieces touch each other |
| ⚠ | visual-fit | these visual meshes reach beyond their collision geometry: what's drawn there doesn't collide, and what rests on it looks sunk in: top_door by 162.3 mm, body by 61.8 mm, bottom_door by 52.7 mm |
| ✓ | welded-bodies | 1 child bodies have no joint and move with their parent: body |
| ✓ | joints | 2 hinge or slide joints: top_door_hinge (hinge, range [0.0, 2.3], damping 0.01), bottom_door_hinge (hinge, range [0.0, 2.5], damping 0.01) |
| ✓ | fixed-to-world | its root is fixed to the world, as furniture is (kept with --keep-fixed) |
| ✓ | starts-apart | no body starts inside another |
| ✓ | settles | after 2 s on the floor nothing moves more than 0.0 mm or 0.0° in 0.2 s |
| ✓ | no-overlap | settling: deepest at any moment none; once settled, between bodies none, into the floor none (allowed: 0.5 mm between bodies and 1 mm into the floor at rest, 3 mm at any moment) |
| ✓ | joints-hold | left alone for 2 s, none of the 2 joints moves more than 5° or 5 mm |
| ✓ | joints-reach | each of the 2 limited joints reaches both ends of its range when driven |

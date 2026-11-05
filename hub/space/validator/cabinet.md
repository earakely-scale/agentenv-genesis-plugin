# cabinet: fail

`cabinet.xml` (cabinet.xml), MuJoCo 3.17.0, 4 bodies, 29.0 kg, timestep 2 ms

| | Check | What it found |
|---|---|---|
| ✓ | loads | loads in MuJoCo 3.17.0: 4 bodies, 11 geoms, 2 joints |
| ✓ | mass-source | every colliding body has an explicit <inertial> |
| ✓ | density | mass over the volume MuJoCo collides (convex hulls, so a hollow object reads low): left_door 208 kg/m³ (2.0 kg), right_door 208 kg/m³ (2.0 kg), body 286 kg/m³ (25.0 kg) |
| ⚠ | inertia | rotational inertia against a solid of the same mass filling its collision hull (1 = the same): left_door 11.27×, right_door 11.27×, body 0.23× |
| ⚠ | centre-of-mass | centre of mass outside the body: body at [0.0, 0.0, 0.0] |
| ⚠ | friction | 7 of 7 colliding geoms have MuJoCo's default friction (1.0 sliding): none is set from a material |
| ✓ | contact-softness | 7 of 7 colliding geoms use MuJoCo's default contact softness (solref 0.02 s) |
| ✓ | hull-fill | no colliding mesh loses much to its convex hull |
| ✓ | floating-pieces | every body's collision pieces touch each other |
| ⚠ | visual-fit | these visual meshes reach beyond their collision geometry: what's drawn there doesn't collide, and what rests on it looks sunk in: body by 267.6 mm, left_door by 29.6 mm, right_door by 29.6 mm |
| ✓ | welded-bodies | 1 child bodies have no joint and move with their parent: body |
| ✓ | joints | 2 hinge or slide joints: left_slide (slide, range [0.0, 0.8], damping 0.01), right_slide (slide, range [-0.76, 0.0], damping 0.01) |
| ✓ | fixed-to-world | its root is fixed to the world, as furniture is (kept with --keep-fixed) |
| ✓ | starts-apart | no body starts inside another |
| ✓ | settles | after 2 s on the floor nothing moves more than 0.0 mm or 0.0° in 0.2 s |
| ✓ | no-overlap | settling: deepest at any moment none; once settled, between bodies none, into the floor none (allowed: 0.5 mm between bodies and 1 mm into the floor at rest, 3 mm at any moment) |
| ✓ | joints-hold | left alone for 2 s, none of the 2 joints moves more than 5° or 5 mm |
| ✗ | joints-reach | driven to the ends of their range, these stop short: left_slide upper end 5% (left_door 3.53 mm into right_door), right_slide lower end 5% (left_door 3.41 mm into right_door) |

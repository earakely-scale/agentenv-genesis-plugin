# Chef_Style_Round_Cake_Pan_9_inch_pan: fail

`Chef_Style_Round_Cake_Pan_9_inch_pan` (model.xml), MuJoCo 3.17.0, 1 bodies, 3.1404 kg, timestep 2 ms

| | Check | What it found |
|---|---|---|
| ✓ | loads | loads in MuJoCo 3.17.0: 1 body, 34 geoms, 1 joint (with the free joints added here) |
| ⚠ | fixed-to-world | model has no joint, so MuJoCo welds it to the world and it will never move. Add <freejoint/> for a free object. The dynamic checks here add one. |
| ✗ | mass-source | 1 body gets mass and inertia from its geoms (no <inertial>), at density 1000 kg/m³ (MuJoCo's default, water); the visual geoms count too: model 88% of the mass is the visual mesh |
| ✓ | density | mass over the volume MuJoCo collides (convex hulls, so a hollow object reads low): model 8788 kg/m³ (3.1404 kg) |
| ✓ | inertia | rotational inertia against a solid of the same mass filling its collision hull (1 = the same): model 1.08× |
| ✓ | centre-of-mass | every body's centre of mass is within its collision geometry's bounds |
| ⚠ | friction | 32 of 32 colliding geoms have MuJoCo's default friction (1.0 sliding): none is set from a material |
| ✓ | contact-softness | 32 of 32 colliding geoms use MuJoCo's default contact softness (solref 0.02 s) |
| ✓ | hull-fill | no colliding mesh loses much to its convex hull |
| ✓ | floating-pieces | every body's collision pieces touch each other |
| ✓ | starts-apart | no body starts inside another |
| ✓ | settles | after 2 s on the floor the fastest part moves at 0.000 m/s |
| ✓ | no-overlap | settling: deepest at any moment model 1.64 mm into floor at 0.04 s; once settled, between bodies none, into the floor model 0.55 mm into floor at 2.0 s (allowed: 0.5 mm between bodies and 1 mm into the floor at rest, 3 mm at any moment) |
| ✓ | timestep | settled at 2 ms and at 1 ms steps, it ends 0.0 mm and 0.0° apart |
| ✓ | tip-margin | model can tilt 79.1° toward its least stable side before its centre of mass passes the edge of its footprint |
| ⚠ | drop | dropped from 10 cm it bounces back 0% of the way and sinks 10.5 mm into the floor on impact (its geoms keep MuJoCo's soft default contacts; solref around 0.003 with a 1 ms step keeps it under 2 mm) |

# Cole_Hardware_Mug_Classic_Blue (OBJ alone): warn

`model.obj` (model.xml), MuJoCo 3.17.0, 1 bodies, 1.5422 kg, timestep 2 ms

| | Check | What it found |
|---|---|---|
| ✓ | loads | loads in MuJoCo 3.17.0: 1 body, 2 geoms, 1 joint |
| ⚠ | mass-source | 1 body gets mass and inertia from its geoms (no <inertial>), at density 1000 kg/m³ (MuJoCo's default, water) |
| ✓ | density | mass over the volume MuJoCo collides (convex hulls, so a hollow object reads low): model 1333 kg/m³ (1.5422 kg) |
| ✓ | inertia | rotational inertia against a solid of the same mass filling its collision hull (1 = the same): model 0.89× |
| ✓ | centre-of-mass | every body's centre of mass is within its collision geometry's bounds |
| ⚠ | friction | 1 of 1 colliding geoms have MuJoCo's default friction (1.0 sliding): none is set from a material |
| ✓ | contact-softness | 1 of 1 colliding geoms use MuJoCo's default contact softness (solref 0.02 s) |
| ⚠ | hull-fill | these meshes collide as their convex hull, which holds much more than the mesh does (a cavity or slot in them won't exist for contact): model/geom1 2.86× |
| ✓ | floating-pieces | every body's collision pieces touch each other |
| ✓ | starts-apart | no body starts inside another |
| ✓ | settles | after 2 s on the floor the fastest part moves at 0.012 m/s |
| ✓ | no-overlap | settling: deepest at any moment model 1.58 mm into floor at 0.04 s; once settled, between bodies none, into the floor model 0.7 mm into floor at 2.0 s (allowed: 0.5 mm between bodies and 1 mm into the floor at rest, 3 mm at any moment) |
| ✓ | timestep | settled at 2 ms and at 1 ms steps, it ends 0.0 mm and 0.0° apart |
| ✓ | tip-margin | model can tilt 34.6° toward its least stable side before its centre of mass passes the edge of its footprint |
| ⚠ | drop | dropped from 10 cm it bounces back 0% of the way and sinks 11.8 mm into the floor on impact (its geoms keep MuJoCo's soft default contacts; solref around 0.003 with a 1 ms step keeps it under 2 mm) |

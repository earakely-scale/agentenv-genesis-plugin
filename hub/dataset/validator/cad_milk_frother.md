# cad_milk_frother: pass

`cad_milk_frother` (scene.xml), MuJoCo 3.17.0, 1 bodies, 0.6636 kg, timestep 1 ms

| | Check | What it found |
|---|---|---|
| ✓ | loads | loads in MuJoCo 3.17.0: 1 body, 75 geoms, 1 joint |
| ✓ | mass-source | every colliding body has an explicit <inertial> |
| ✓ | density | mass over the volume MuJoCo collides (convex hulls, so a hollow object reads low): cad_milk_frother/cad_milk_frother 1967 kg/m³ (0.6636 kg) |
| ✓ | inertia | rotational inertia against a solid of the same mass filling its collision hull (1 = the same): cad_milk_frother/cad_milk_frother 1.07× |
| ✓ | centre-of-mass | every body's centre of mass is within its collision geometry's bounds |
| ✓ | friction | 0 of 71 colliding geoms have MuJoCo's default friction (1.0 sliding): the rest are set |
| ✓ | contact-softness | 0 of 71 colliding geoms use MuJoCo's default contact softness (solref 0.02 s) |
| ✓ | hull-fill | no colliding mesh loses much to its convex hull |
| ✓ | floating-pieces | every body's collision pieces touch each other |
| ✓ | starts-apart | no body starts inside another |
| ✓ | settles | after 2 s on the floor the fastest part moves at 0.004 m/s |
| ✓ | no-overlap | settling: deepest at any moment none; once settled, between bodies none, into the floor none (allowed: 0.5 mm between bodies and 1 mm into the floor at rest, 3 mm at any moment) |
| ✓ | timestep | settled at 1 ms and at 0.5 ms steps, it ends 0.0 mm and 0.0° apart |
| ✓ | tip-margin | cad_milk_frother/cad_milk_frother can tilt 36.7° toward its least stable side before its centre of mass passes the edge of its footprint |
| ✓ | drop | dropped from 10 cm it bounces back 0% of the way and sinks 0.0 mm into the floor on impact |

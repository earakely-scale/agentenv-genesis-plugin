# knife_block: pass

`knife_block.zip` (scene.xml), MuJoCo 3.17.0, 7 bodies, 3.534 kg, timestep 1 ms

| | Check | What it found |
|---|---|---|
| ✓ | loads | loads in MuJoCo 3.17.0: 7 bodies, 61 geoms, 7 joints |
| ✓ | mass-source | every colliding body has an explicit <inertial> |
| ✓ | density | mass over the volume MuJoCo collides (convex hulls, so a hollow object reads low): knife_block/knife_block 711 kg/m³ (2.5405 kg), paring_9cm/paring_9cm 2527 kg/m³ (0.0814 kg), boning_15cm/boning_15cm 2548 kg/m³ (0.1217 kg), utility_13cm/utility_13cm 2728 kg/m³ (0.0938 kg), bread_20cm/bread_20cm 2921 kg/m³ (0.2026 kg), santoku_17cm/santoku_17cm 3190 kg/m³ (0.218 kg) |
| ✓ | inertia | rotational inertia against a solid of the same mass filling its collision hull (1 = the same): bread_20cm/bread_20cm 1.17×, chef_20cm/chef_20cm 1.14×, santoku_17cm/santoku_17cm 1.14×, paring_9cm/paring_9cm 0.91×, boning_15cm/boning_15cm 0.95×, knife_block/knife_block 1.0× |
| ✓ | centre-of-mass | every body's centre of mass is within its collision geometry's bounds |
| ✓ | friction | 0 of 40 colliding geoms have MuJoCo's default friction (1.0 sliding): the rest are set |
| ✓ | contact-softness | 0 of 40 colliding geoms use MuJoCo's default contact softness (solref 0.02 s) |
| ✓ | hull-fill | no colliding mesh loses much to its convex hull |
| ✓ | floating-pieces | every body's collision pieces touch each other |
| ✓ | starts-apart | no body starts inside another |
| ✓ | settles | after 2 s on the floor the fastest part moves at 0.003 m/s |
| ✓ | no-overlap | settling: deepest at any moment none; once settled, between bodies none, into the floor none (allowed: 0.5 mm between bodies and 1 mm into the floor at rest, 3 mm at any moment) |
| ✓ | timestep | settled at 1 ms and at 0.5 ms steps, it ends 0.0 mm and 0.0° apart |
| ✓ | tip-margin | knife_block/knife_block with the 6 bodies resting on it can tilt 13.4° toward its least stable side before its centre of mass passes the edge of its footprint |
| ✓ | drop | dropped from 10 cm it bounces back 8% of the way and sinks 1.6 mm into the floor on impact |

"""Compile Genesis's kernels and process every asset's meshes at image build time, so a fresh container's first probe
builds in about a second."""

import assets as A

for name in ["mug_017", "oven_004", "cabinet_022"] + A.available():
    obj = A.load(name)
    obj.checks("original")
    if name == "cabinet_022":
        obj.model["joints"]["drawer_slide"] = {"type": "slide", "parent": "carcass", "child": "drawer", "axis": [0, -1, 0],
                                               "range": [0, 0.42], "damping": 3, "frictionloss": 0.5}
        obj.checks("current")
    if obj.kind != "primitive":
        obj.render()
    print("warm", name, flush=True)

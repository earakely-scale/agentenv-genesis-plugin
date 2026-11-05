"""Compile Genesis's kernels, run every asset's checks in MuJoCo and Genesis, and process its meshes at image build
time, so a fresh container's first probe builds in about a second."""

import assets as A

for name in ["mug_017", "oven_004", "cabinet_022"] + A.available():
    obj = A.load(name)
    obj.checks("original")
    if obj.kind in ("mesh", "set"):
        obj.checks("original", "genesis")  # Genesis's second opinion
    if name == "cabinet_022":
        obj.model["joints"]["drawer_slide"] = {"type": "slide", "parent": "carcass", "child": "drawer", "axis": [0, -1, 0],
                                               "range": [0, 0.42], "damping": 3, "frictionloss": 0.5}
        obj.checks("current")
    if obj.kind != "primitive":
        obj.render()
        A.web_scene(obj)
    print("warm", name, flush=True)

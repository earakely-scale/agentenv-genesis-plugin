"""Compile Genesis's kernels at image build time, so a fresh container's first probe builds in about a second."""

import workbench as wb

sim = wb.Sim()
for name in ("mug_017", "oven_004", "cabinet_022"):
    model = wb.initial_model(name)
    wb.suite(sim, model)
    if name == "cabinet_022":
        model["joints"]["drawer_slide"] = {"type": "slide", "parent": "carcass", "child": "drawer", "axis": [0, -1, 0],
                                           "range": [0, 0.42], "damping": 3, "frictionloss": 0.5}
        wb.suite(sim, model)
print("warm")

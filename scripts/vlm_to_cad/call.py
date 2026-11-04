"""Ask a VLM, through an Anthropic Messages API endpoint, for CadQuery code that builds the object in the photos.

The endpoint is `$ANTHROPIC_BASE_URL/v1/messages` with the key in `$ANTHROPIC_API_KEY`. The prompt gives the
category label and the grid spacing only, never the product name or its real size.
"""

import base64
import json
import os
import re
import urllib.request

MODEL = "anthropic/claude-sonnet-5-5"
MAX_TOKENS = 8000
MATERIALS = [
    "ceramic", "porcelain", "glass", "stainless_steel", "cast_iron",
    "aluminum", "plastic_pp", "plastic_abs", "wood", "silicone",
]

SYSTEM = (
    "You are a CAD engineer who builds simulation-ready 3D models of household objects from photos. "
    "You write CadQuery Python code."
)

PROMPT = """These two photos (a three-quarter view and a side view) show one kitchen object. Category: {label}.

The object stands on a grid of 1 cm x 1 cm squares; every fifth line is darker (5 cm). Use the grid to estimate its real dimensions.

Write a CadQuery (cadquery 2.8) Python script that models this object for a physics simulation:
- Units are metres. Z is up. The object rests on the XY plane with its base centred at the origin (lowest point at z = 0).
- Model wall thickness and hollow interiors realistically.
- Split the object by material: define a module-level variable `parts = [(cq_object, "material"), ...]` with one entry per material region, where each cq_object is a cq.Workplane or cq.Shape holding solid geometry.
- Choose each material from: {materials}.
- Import only cadquery (as cq) and the Python standard library. Do not read or write files, and do not export anything.

Return only the Python code, with no explanation and no markdown fences."""

REPAIR = """Running your script failed:

{error}

Fix the script. Return only the complete corrected Python code, with no explanation and no markdown fences."""


def _image_block(path: str) -> dict:
    with open(path, "rb") as f:
        data = base64.b64encode(f.read()).decode()
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}


def first_messages(label: str, image_paths: list[str]) -> list[dict]:
    content = [_image_block(p) for p in image_paths]
    content.append({"type": "text", "text": PROMPT.format(label=label, materials=", ".join(MATERIALS))})
    return [{"role": "user", "content": content}]


def repair_messages(messages: list[dict], code: str, error: str) -> list[dict]:
    return messages + [
        {"role": "assistant", "content": code},
        {"role": "user", "content": REPAIR.format(error=error[-4000:])},
    ]


def extract_code(text: str) -> str:
    m = re.search(r"```(?:python)?\s*\n(.*?)```", text, re.S)
    return (m.group(1) if m else text).strip() + "\n"


def complete(messages: list[dict], timeout: float = 300.0) -> tuple[str, dict]:
    """One Messages API call; returns the reply's text and its usage."""
    body = json.dumps({"model": MODEL, "max_tokens": MAX_TOKENS, "system": SYSTEM, "messages": messages}).encode()
    req = urllib.request.Request(
        os.environ["ANTHROPIC_BASE_URL"].rstrip("/") + "/v1/messages",
        data=body,
        headers={
            "x-api-key": os.environ["ANTHROPIC_API_KEY"],
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        out = json.load(resp)
    text = "".join(b.get("text", "") for b in out.get("content", []) if b.get("type") == "text")
    usage = out.get("usage", {})
    return text, {"input_tokens": int(usage.get("input_tokens", 0)), "output_tokens": int(usage.get("output_tokens", 0))}

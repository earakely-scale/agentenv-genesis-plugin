"""Grade the edited object with the workbench's hidden Genesis probes. Set GENESIS_EPISODES_DIR to also keep each
episode (calls, renders, MJCF before and after, probe curves) as a JSON file for publishing."""

import json
import os
import time

from agentenv_protocol import client


async def verify(mcp_url: str) -> list[dict]:
    episode = (await client.get_data(mcp_url.removesuffix("/mcp"))).parts[0].data
    out = os.environ.get("GENESIS_EPISODES_DIR")
    if out and episode.get("object"):
        os.makedirs(out, exist_ok=True)
        path = os.path.join(out, f"{episode['object']}-{time.strftime('%Y%m%dT%H%M%S')}.json")
        with open(path, "w") as f:
            json.dump(episode, f)
    return [{"id": c["id"], "criterion": c["description"], "result": bool(c["result"]),
             "score": 1.0 if c["result"] else 0.0} for c in episode.get("checks", [])] or [
        {"id": "loaded", "criterion": "an object was loaded", "result": False, "score": 0.0}]

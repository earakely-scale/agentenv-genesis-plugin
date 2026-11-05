"""The workbench's task steps: `workbench_session` puts a contributor in the loop, and `save_sim_asset` keeps the object
they approved.

`workbench_session` is prompt_agent with the contributor as the agent's counterpart. Its user side is the env's A2A peer
(/workbench/peer), which answers each of the agent's replies with the contributor's next chat message from the page at
/workbench, and ends the conversation once they approve or reject the object. A prompt holding {contributor} waits for
the contributor's first message and opens with it."""

from __future__ import annotations

import base64
import io
import json
import logging
import uuid
import zipfile
from typing import ClassVar

from agent_env.a2a_agent.object_transfer import send_and_wait
from agent_env.artifact import FileArtifact
from agent_env.entity_refs import EntityRef
from agent_env.env.env import DeployedEnv
from agent_env.task_step.context import TaskStepContext
from agent_env.task_step.task_step import TaskStep
from agent_env.task_step.task_steps.prompt_agent import PromptAgentTaskStep
from agentenv_protocol import client

EXPORT = "urn:genesis:session-export/v1"
DEFAULT_ENV = "genesis-workbench"

log = logging.getLogger(__name__)


class WorkbenchSessionTaskStep(PromptAgentTaskStep):
    """prompt_agent whose counterpart is the contributor on the env's page (`env_id`): up to `max_conversation_turns`
    (40 by default) replies, each waiting up to `user_agent_timeout_seconds` (30 min by default) for the contributor.
    The page's address goes in the run's `metadata["workbench"]` and in the log, as a `WORKBENCH` line."""

    type: ClassVar[str] = "workbench_session"
    entity_refs = (*PromptAgentTaskStep.entity_refs, EntityRef.env("env_id"))
    DEFAULTS: ClassVar[dict] = {"max_conversation_turns": 40, "user_agent_timeout_seconds": 1800}

    def __init__(self, *args, env_id: str = DEFAULT_ENV, **kwargs):
        super().__init__(*args, **{**self.DEFAULTS, **kwargs})
        self.env_id = env_id

    def to_dict(self) -> dict:
        return {**super().to_dict(), "env_id": self.env_id}

    @classmethod
    def from_dict(cls, data: dict) -> WorkbenchSessionTaskStep:
        step = super().from_dict({**cls.DEFAULTS, **data})
        step.env_id = data.get("env_id", DEFAULT_ENV)
        return step

    async def execute(self, context: TaskStepContext) -> TaskStepContext:
        base = _deployed(context, self.env_id).environment_url
        page = f"{base}/workbench"
        context.metadata.setdefault("workbench", {})[self.id] = record = {"page": page}
        log.warning("WORKBENCH contributor page for %s: %s", self.env_id, page)
        prompt, peer = self.prompt, self.user_a2a_url
        self.user_a2a_url = f"{base}/workbench/peer"
        try:
            if self.prompt and "{contributor}" in self.prompt:
                _, result = await send_and_wait(
                    self.user_a2a_url, [{"kind": "data", "data": {"workbench": "open"}}], agent=None,
                    message_id=uuid.uuid4().hex, context_id=None, timeout_seconds=self.user_agent_timeout_seconds,
                    poll_interval_seconds=self.poll_interval_seconds)
                state = result["status"]["state"]
                if state != "completed":
                    record["ended"] = f"the contributor's first message never came ({state})"
                    return context
                first = "\n".join(p.get("text", "") for p in result["status"]["message"]["parts"] if p.get("kind") == "text")
                self.prompt = self.prompt.replace("{contributor}", first)
            return await super().execute(context)
        finally:
            self.prompt, self.user_a2a_url = prompt, peer


class SaveSimAssetTaskStep(TaskStep):
    """Keep the object the contributor approved on `env_id` as a new version of the file artifact `artifact_id` (its
    `{object}` filled in): a zip of the edited asset.json, its model file, the acceptance checks and probe results run
    on it at approval, and the session (chat, the contributor's actions, the agent's calls). The artifact's metadata
    carries the checks' outcome. An object that wasn't approved writes nothing; the run's `metadata["sim_assets"]` says
    what happened either way."""

    type: ClassVar[str] = "save_sim_asset"
    entity_refs = (EntityRef.env("env_id"),)

    def __init__(self, id: str, version: int | None, env_id: str = DEFAULT_ENV,
                 artifact_id: str = "@local/genesis-workbench/sim-assets/{object}", timeout_seconds: int = 900,
                 depends_on: list | None = None, fail_task_on_error: bool = True):
        super().__init__(id, version, depends_on=depends_on, fail_task_on_error=fail_task_on_error)
        self.env_id, self.artifact_id, self.timeout_seconds = env_id, artifact_id, timeout_seconds

    def to_dict(self) -> dict:
        return {**super().to_dict(), "env_id": self.env_id, "artifact_id": self.artifact_id,
                "timeout_seconds": self.timeout_seconds}

    @classmethod
    def from_dict(cls, data: dict) -> SaveSimAssetTaskStep:
        return cls(**cls._base_from_dict(data), **{k: data[k] for k in ("env_id", "artifact_id", "timeout_seconds") if k in data})

    async def execute(self, context: TaskStepContext) -> TaskStepContext:
        deployed = _deployed(context, self.env_id)
        export = await client.invoke_extension(deployed.environment_url, deployed.environment_card, EXPORT, {},
                                               timeout=self.timeout_seconds)
        record = {"object": export.get("object"), "status": export["status"]}
        if export["status"] == "approved":
            name = export["object"]
            art = FileArtifact.put_bytes(
                self.artifact_id.format(object=name), filename=f"{name}.zip", content=_bundle(export),
                content_type="application/zip",
                description=f"The {export['label']} {name}, approved by a contributor in the Genesis workbench")
            decision = export["decision"]
            art.merge_metadata({"object": name, "kind": export["kind"], "label": export["label"],
                                "passed": export["passed"], "acceptance": {r["id"]: r["result"] for r in export["acceptance"]},
                                "agent_edits": decision["object_edits"], "approval_note": decision["note"]})
            record.update({"artifact_id": art.id, "artifact_version": art.version, "passed": export["passed"]})
        context.metadata.setdefault("sim_assets", {})[self.id] = record
        log.warning("SIM_ASSET %s", json.dumps(record))
        return context


def _bundle(export: dict) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("asset.json", json.dumps(export["asset"], indent=1))
        z.writestr(export["model_filename"], export["model_text"])
        z.writestr("checks.json", json.dumps({k: export[k] for k in ("acceptance", "passed", "probes")}, indent=1))
        z.writestr("session.json", json.dumps({k: export[k] for k in ("decision", "diff", "chat", "interactions", "agent_calls")},
                                              indent=1))
        if export.get("mujoco_export"):  # the MJCF and OBJ files the object ships as, checked to load in plain MuJoCo
            with zipfile.ZipFile(io.BytesIO(base64.b64decode(export["mujoco_export"]["zip_b64"]))) as m:
                for item in m.infolist():
                    if not item.is_dir():
                        z.writestr("mujoco/" + item.filename.split("/", 1)[-1], m.read(item))
        for which, name in (("current", "scene"), ("original", "as_delivered")):
            if f"scene_{which}" in export:
                z.writestr(f"{name}.glb", base64.b64decode(export[f"scene_{which}"]["glb_b64"]))
                z.writestr(f"{name}.json", json.dumps(export[f"scene_{which}"]["meta"]))
        z.writestr("README.txt", f"{export['label']} {export['object']} as the contributor approved it. Meshes and textures are "
                                 "the source asset's, unchanged; asset.json holds the edits and model file the result. mujoco/ holds it as MJCF and OBJ for MuJoCo (scene.xml loads it). scene.glb is the approved object "
                                 "as the workbench's 3D view draws it, one node per visual geom posed by scene.json; as_delivered.glb is the same "
                                 "before the edits.\n")
    return buf.getvalue()


def _deployed(context: TaskStepContext, env_id: str) -> DeployedEnv:
    deployed = next((d for d in context.deployed_envs if d.env_id == env_id), None)
    if deployed is None:
        raise RuntimeError(f"env {env_id!r} is not deployed in this run")
    return deployed

"""Start a release: one in-flight pipeline per environment, then the GitLab call."""

from datetime import timedelta

from fastapi import APIRouter, HTTPException, Request

from app import startup
from config import Config
from gitlab_client import get_pipeline_status, trigger_pipeline
from logging_utils import audit_log, logger
from model import DeploymentRequest
from releases_store import (
    TERMINAL_PIPELINE_STATUSES,
    EnvironmentBusy,
    clear_environment_lock,
    get_environment_lock,
    latest_release_for_environment,
    note_pipeline_running,
    record_release,
    release_environment,
    release_stale_reservation,
    reserve_environment,
    set_pipeline_status,
)

router = APIRouter()

_RESERVATION_TTL = timedelta(minutes=3)


def _change_summary(req: DeploymentRequest) -> str:
    if not req.taint:
        return ""
    if req.pipelineTrigger == "tenant_baseline":
        refresh = "refresh" if req.tfRefresh else "no-refresh"
        return f"replace {req.replaceResource} ({refresh})"
    if req.pipelineTrigger == "kasm_vdi":
        return f"replace state {req.replaceState}"
    if req.pipelineTrigger == "tenant_services":
        return "services " + ",".join(req.services or [])
    return ""


def _pipeline_id(gitlab_response: object) -> int | None:
    if not isinstance(gitlab_response, dict):
        return None
    raw_id = gitlab_response.get("id")
    try:
        pipeline_id = int(raw_id)
    except (TypeError, ValueError):
        return None
    if pipeline_id < 1:
        return None
    return pipeline_id


async def _observed_status(pipeline_id: int) -> str:
    try:
        live = await get_pipeline_status(startup.gitlab_token, pipeline_id)
    except HTTPException as exc:
        if exc.status_code != 404:
            raise
        set_pipeline_status(pipeline_id, "unknown")
        return "unknown"
    status = live.get("status") or ""
    if status:
        set_pipeline_status(pipeline_id, status)
    return status


async def _ensure_environment_available(environment: str) -> str:
    release_stale_reservation(environment, _RESERVATION_TTL)
    lock = get_environment_lock(environment)
    if lock and lock.get("state") == "reserving":
        raise HTTPException(status_code=409, detail=str(EnvironmentBusy(environment, reserving=True)))

    pipeline_id = None
    if lock and lock.get("gitlab_pipeline_id"):
        pipeline_id = int(lock["gitlab_pipeline_id"])
    else:
        latest = latest_release_for_environment(environment)
        if latest and latest.get("gitlab_pipeline_id"):
            if latest.get("pipeline_status") not in TERMINAL_PIPELINE_STATUSES:
                pipeline_id = int(latest["gitlab_pipeline_id"])

    if pipeline_id:
        status = await _observed_status(pipeline_id)
        if status not in TERMINAL_PIPELINE_STATUSES:
            raise HTTPException(status_code=409, detail=str(EnvironmentBusy(environment, pipeline_id)))
        clear_environment_lock(pipeline_id)

    try:
        return reserve_environment(environment)
    except EnvironmentBusy as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/api/trigger-deployment")
async def trigger_deployment(req: DeploymentRequest, request: Request):
    username = (request.session.get("user") or {}).get("preferred_username", "unknown")

    audit_log(
        "deployment_trigger_attempt",
        username=username,
        detail=(
            f"env={req.targetEnvironment} trigger={req.pipelineTrigger} "
            f"tenants={req.tenantList} taint={req.taint} cr={req.crNumber or '-'}"
        ),
    )
    reservation = None
    gitlab_response = None
    pipeline_id = None
    try:
        reservation = await _ensure_environment_available(req.targetEnvironment)
        gitlab_response = await trigger_pipeline(startup.gitlab_token, req, username)
        pipeline_id = _pipeline_id(gitlab_response)
        held = True
        if pipeline_id:
            held = note_pipeline_running(req.targetEnvironment, pipeline_id, reservation)
        web_url = gitlab_response.get("web_url") if isinstance(gitlab_response, dict) else None
        pipeline_status = gitlab_response.get("status") if isinstance(gitlab_response, dict) else None
        record_release(
            username=username,
            environment=req.targetEnvironment,
            tenant_list=req.tenantList,
            pipeline_trigger=req.pipelineTrigger,
            taint=req.taint,
            cr_number=req.crNumber,
            change_summary=_change_summary(req),
            gitlab_pipeline_id=pipeline_id,
            gitlab_web_url=web_url if isinstance(web_url, str) else None,
            pipeline_status=pipeline_status or "created",
        )
        if pipeline_id and not held:
            raise HTTPException(
                status_code=500,
                detail=(
                    f"GitLab accepted the pipeline (id={pipeline_id}) but this environment already has another release. "
                    "Do not retry until that pipeline finishes."
                ),
            )
    except HTTPException:
        if pipeline_id is None and reservation:
            release_environment(req.targetEnvironment, reservation)
        raise
    except Exception as exc:
        if pipeline_id is None and reservation:
            release_environment(req.targetEnvironment, reservation)
            raise
        # The pipeline already exists. Say so plainly so the operator does not fire a second one.
        audit_log("release_record_failed", username=username, detail=str(exc))
        logger.warning("Release ledger write failed after GitLab accepted pipeline %s: %s", pipeline_id, exc)
        raise HTTPException(
            status_code=500,
            detail=(
                f"GitLab accepted the pipeline (id={pipeline_id}) but it was not written to the release ledger. "
                "Do not retry until the ledger is healthy."
            ),
        )

    audit_log(
        "deployment_trigger_success",
        username=username,
        detail=f"env={req.targetEnvironment} pipeline={pipeline_id} cr={req.crNumber or '-'}",
    )

    pipelines_url = Config.GITLAB_ORCHESTRATOR_PIPELINES_URL.strip()
    if not pipelines_url.startswith(("http://", "https://")):
        pipelines_url = ""

    return {
        "status": "accepted",
        "message": f"Pipeline triggered for {req.targetEnvironment}!",
        "pipelineId": pipeline_id,
        "orchestratorPipelinesUrl": pipelines_url,
    }

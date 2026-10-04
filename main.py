import os
import sys
from contextlib import asynccontextmanager
from datetime import timedelta
from fastapi import FastAPI, HTTPException, Request
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response

from model import DeploymentRequest
from gitlab_client import get_pipeline_status, trigger_pipeline
from config import Config, assert_runtime_config
from auth import router as auth_router, PUBLIC_PATHS
from logging_utils import audit_log, logger
from releases_store import (
    TERMINAL_PIPELINE_STATUSES,
    EnvironmentBusy,
    clear_environment_lock,
    get_environment_lock,
    init_db,
    latest_release_for_environment,
    list_prod_audit,
    list_releases,
    note_pipeline_running,
    portal_started_pipeline,
    prod_audit_csv,
    record_release,
    release_environment,
    release_stale_reservation,
    reserve_environment,
    set_pipeline_status,
)

gitlab_token = ""

@asynccontextmanager
async def lifespan(app: FastAPI):
    global gitlab_token
    try:
        assert_runtime_config()
        # Using variables from config.py
        if os.path.exists(Config.TOKEN_PATH):
            with open(Config.TOKEN_PATH, "r") as file:
                gitlab_token = file.read().strip()
            print("✅ GitLab token loaded from Kubernetes volume mount.")
        elif Config.LOCAL_GITLAB_TOKEN:
            gitlab_token = Config.LOCAL_GITLAB_TOKEN
            print("⚠️ Local Mode: GitLab token loaded from environment variable.")
        else:
            raise FileNotFoundError(f"Missing {Config.TOKEN_PATH} and GITLAB_TOKEN env var.")
        init_db()
        print("Release ledger ready.")
    except Exception as e:
        print(f"FATAL STARTUP ERROR: {e}")
        sys.exit(1)
    
    yield

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    allowed = {Config.PORTAL_PUBLIC_URL, str(request.base_url).rstrip("/")}
    return origin.rstrip("/") in allowed


@app.middleware("http")
async def require_auth(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"} and not _same_origin(request):
        return JSONResponse({"detail": "Cross-origin request refused."}, status_code=403)
    if request.url.path in PUBLIC_PATHS:
        return await call_next(request)
    if not request.session.get("user"):
        if request.url.path.startswith("/api/"):
            return JSONResponse({"detail": "Not authenticated"}, status_code=401)
        return RedirectResponse("/login?reason=unauthorized")
    return await call_next(request)


# Registered after require_auth so it wraps around it, applying headers to its
# early-return redirects/401s too, not just responses from route handlers.
@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
    response.headers["Cross-Origin-Opener-Policy"] = "same-origin"
    response.headers["Cross-Origin-Resource-Policy"] = "same-origin"
    response.headers["Cache-Control"] = "no-store"
    # Tailwind's CDN build injects its own <style>, so style-src needs 'unsafe-inline'.
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; "
        "script-src 'self' https://cdn.tailwindcss.com 'unsafe-inline'; "
        "style-src 'self' https://cdn.tailwindcss.com 'unsafe-inline'; "
        "img-src 'self' data:; "
        "connect-src 'self'"
    )
    if Config.SESSION_COOKIE_SECURE:
        response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
    return response


# Added last so it wraps (runs before) everything else, populating request.session first.
app.add_middleware(
    SessionMiddleware,
    secret_key=Config.SESSION_SECRET_KEY,
    same_site="lax",
    https_only=Config.SESSION_COOKIE_SECURE,
    max_age=Config.SESSION_MAX_AGE_SECONDS,
)
app.include_router(auth_router)

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


@app.post("/api/trigger-deployment")
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
        gitlab_response = await trigger_pipeline(gitlab_token, req, username)
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


@app.get("/api/pipelines/{pipeline_id}")
async def pipeline_status(pipeline_id: int):
    if pipeline_id < 1 or not portal_started_pipeline(pipeline_id):
        raise HTTPException(status_code=404, detail="Pipeline not found.")
    result = await get_pipeline_status(gitlab_token, pipeline_id)
    status = result.get("status")
    if status:
        set_pipeline_status(pipeline_id, status)
        if status in TERMINAL_PIPELINE_STATUSES:
            clear_environment_lock(pipeline_id)
    return result


_RESERVATION_TTL = timedelta(minutes=3)


async def _observed_status(pipeline_id: int) -> str:
    try:
        live = await get_pipeline_status(gitlab_token, pipeline_id)
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


_NO_STORE = {"Cache-Control": "no-store"}


@app.get("/api/releases")
async def get_releases():
    return JSONResponse({"releases": list_releases()}, headers=_NO_STORE)


@app.get("/api/releases/audit")
async def get_prod_audit():
    return JSONResponse({"releases": list_prod_audit()}, headers=_NO_STORE)


@app.get("/api/releases/audit.csv")
async def download_prod_audit():
    return Response(
        content=prod_audit_csv(),
        media_type="text/csv; charset=utf-8",
        headers={
            **_NO_STORE,
            "Content-Disposition": 'attachment; filename="prod-releases.csv"',
        },
    )


@app.get("/releases")
async def releases_page():
    return FileResponse("public/releases.html")

app.mount("/", StaticFiles(directory="public", html=True), name="public")
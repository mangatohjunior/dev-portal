import httpx
from fastapi import HTTPException
from model import DeploymentRequest
from config import Config
from logging_utils import logger

async def trigger_pipeline(gitlab_token: str, req: DeploymentRequest, username: str):
    # Every key is always sent so the tenant job can echo the full portal payload.
    gitlab_variables = [
        {"key": "TARGET_ENV", "value": req.targetEnvironment},
        {"key": "TENANT_LIST", "value": req.tenantList},
        {"key": "PIPELINE_TRIGGER", "value": req.pipelineTrigger},
        {"key": "TAINT", "value": str(req.taint).lower()},
        {"key": "TRIGGERED_BY", "value": username},
        {"key": "CR_NUMBER", "value": req.crNumber or ""},
        {"key": "REPLACE_RESOURCE", "value": (req.replaceResource or "") if req.taint else ""},
        {"key": "TF_REFRESH", "value": "" if req.tfRefresh is None or not req.taint else str(req.tfRefresh).lower()},
        {"key": "REPLACE_STATE", "value": (req.replaceState or "") if req.taint else ""},
        {"key": "SERVICES_LIST", "value": ",".join(req.services) if req.taint and req.services else ""},
    ]

    payload = {
        "ref": "main",
        "variables": gitlab_variables
    }

    headers = {
        "PRIVATE-TOKEN": gitlab_token,
        "Content-Type": "application/json"
    }

    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        try:
            # Using the URL from config.py. Redirects are not followed: the token is on this request.
            response = await client.post(Config.GITLAB_API_URL, json=payload, headers=headers)

            if response.status_code not in (200, 201):
                logger.warning("GitLab API error: status %s", response.status_code)
                raise HTTPException(status_code=502, detail="GitLab rejected the pipeline trigger request.")

            return response.json()
        except httpx.RequestError as exc:
            logger.warning("Failed to connect to GitLab: %s", exc)
            raise HTTPException(status_code=502, detail="Failed to connect to GitLab.")


async def get_pipeline_status(gitlab_token: str, pipeline_id: int) -> dict:
    url = f"{Config.GITLAB_PROJECT_API}/pipelines/{pipeline_id}"
    headers = {"PRIVATE-TOKEN": gitlab_token}
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        try:
            response = await client.get(url, headers=headers)
        except httpx.RequestError as exc:
            logger.warning("Failed to read pipeline %s: %s", pipeline_id, exc)
            raise HTTPException(status_code=502, detail="Failed to connect to GitLab.")

    if response.status_code == 404:
        raise HTTPException(status_code=404, detail="Pipeline not found.")
    if response.status_code != 200:
        logger.warning("GitLab pipeline status error: status %s", response.status_code)
        raise HTTPException(status_code=502, detail="GitLab did not return the pipeline status.")

    data = response.json()
    return {"id": data.get("id"), "status": data.get("status")}
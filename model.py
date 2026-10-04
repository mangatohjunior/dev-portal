import re
from typing import List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, model_validator

# Change-request ids such as CHG0012345 or CR-1024. First character is
# alphanumeric so the value cannot be turned into a spreadsheet formula.
_CR_NUMBER = re.compile(r"[A-Za-z0-9][A-Za-z0-9-]{2,31}")
# Same shape the orchestrator accepts before it writes a YAML key.
_TENANT = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,62}")
# Terraform address. No quotes, spaces, or shell metacharacters.
_RESOURCE = re.compile(r"[A-Za-z0-9_.\-\[\]]{1,128}")
_STATE = re.compile(r"[A-Za-z0-9_.-]{1,64}")

# Must stay in step with the checkboxes on the deploy form.
ALLOWED_SERVICES = (
    "airflow",
    "cloudbeaver",
    "gitlab_runner",
    "immuta",
    "jupyterhub",
    "mlflow",
    "s3uploader",
    "superset",
)
_ALLOWED_SERVICES = frozenset(ALLOWED_SERVICES)


class DeploymentRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    targetEnvironment: Literal["dev", "test", "preprod", "prod"]
    allowProd: bool
    tenantList: str = Field(..., min_length=1, max_length=2000)
    pipelineTrigger: Literal["tenant_baseline", "kasm_vdi", "tenant_services"]
    taint: bool

    # Optional fields depending on the trigger
    replaceResource: Optional[str] = Field(default=None, max_length=128)
    tfRefresh: Optional[bool] = None
    replaceState: Optional[str] = Field(default=None, max_length=64)
    services: Optional[List[str]] = None
    # Required for prod. Ignored for every other environment.
    crNumber: Optional[str] = Field(default=None, max_length=32)

    @model_validator(mode="after")
    def constrain_pipeline_inputs(self):
        if self.targetEnvironment == "prod":
            if not self.allowProd:
                raise ValueError("Cannot deploy to Prod: 'Allow Prod Trigger' is not checked.")
            cr = (self.crNumber or "").strip().upper()
            if not _CR_NUMBER.fullmatch(cr):
                raise ValueError("Prod releases require a change request number (e.g. CHG0012345).")
            self.crNumber = cr
        else:
            self.crNumber = None

        names: list[str] = []
        seen: set[str] = set()
        for raw in self.tenantList.split(","):
            name = raw.strip()
            if not name or name in seen:
                continue
            if not _TENANT.fullmatch(name):
                raise ValueError("Tenant names must be letters, numbers, '_' or '-'.")
            seen.add(name)
            names.append(name)
        if not names:
            raise ValueError("At least one tenant is required.")
        if len(names) > 20:
            raise ValueError("A release can include at most 20 tenants.")
        self.tenantList = ",".join(names)

        if not self.taint:
            self.replaceResource = None
            self.tfRefresh = None
            self.replaceState = None
            self.services = None
            return self

        if self.pipelineTrigger == "tenant_baseline":
            resource = (self.replaceResource or "").strip()
            if not _RESOURCE.fullmatch(resource):
                raise ValueError("Replacement resource must be a Terraform address such as module.database.")
            self.replaceResource = resource
            self.tfRefresh = bool(self.tfRefresh)
            self.replaceState = None
            self.services = None
        elif self.pipelineTrigger == "kasm_vdi":
            state = (self.replaceState or "").strip()
            if not _STATE.fullmatch(state):
                raise ValueError("Replace state must be letters, numbers, '.' or '-'.")
            self.replaceState = state
            self.replaceResource = None
            self.tfRefresh = None
            self.services = None
        else:
            chosen: list[str] = []
            for service in self.services or []:
                if service not in _ALLOWED_SERVICES:
                    raise ValueError("One or more selected services are not allowed.")
                if service not in chosen:
                    chosen.append(service)
            if not chosen:
                raise ValueError("At least one service must be selected for Tenant Services.")
            self.services = chosen
            self.replaceResource = None
            self.tfRefresh = None
            self.replaceState = None
        return self
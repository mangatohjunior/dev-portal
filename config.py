import os
from urllib.parse import quote

_APP_DIR = os.path.dirname(os.path.abspath(__file__))

class Config:
    # Local GitLab. The portal triggers tenant_orchestrator, which fans out
    # to the tenant projects. A numeric id also works if set in the environment.
    GITLAB_BASE_URL = os.getenv("GITLAB_BASE_URL", "http://host.docker.internal:8929")
    GITLAB_PROJECT_ID = os.getenv("GITLAB_PROJECT_ID", "root/tenant_orchestrator")

    # Computed API URL. The project path is encoded so the slash survives.
    GITLAB_PROJECT_API = (
        f"{GITLAB_BASE_URL.rstrip('/')}/api/v4/projects/"
        f"{quote(GITLAB_PROJECT_ID, safe='')}"
    )
    GITLAB_API_URL = f"{GITLAB_PROJECT_API}/pipeline"
    # Opened in the operator's browser after a successful launch. This is the
    # external GitLab address, not the in-cluster API host. Override it for prod.
    GITLAB_ORCHESTRATOR_PIPELINES_URL = os.getenv(
        "GITLAB_ORCHESTRATOR_PIPELINES_URL",
        "http://localhost:8929/root/tenant_orchestrator/-/pipelines",
    )
    
    # Security & Token Settings
    TOKEN_PATH = os.getenv("TOKEN_PATH", "/mnt/secrets/gitlab-token")
    LOCAL_GITLAB_TOKEN = os.getenv("GITLAB_TOKEN", "") # Fallback for local testing

    # Keycloak SSO settings
    # Internal: used for container-to-container calls (token exchange, userinfo).
    # External: used for browser redirects, so it must be reachable from the user's machine.
    KEYCLOAK_INTERNAL_URL = os.getenv("KEYCLOAK_INTERNAL_URL", "http://keycloak:8080")
    KEYCLOAK_EXTERNAL_URL = os.getenv("KEYCLOAK_EXTERNAL_URL", "http://localhost:8080")
    KEYCLOAK_REALM = os.getenv("KEYCLOAK_REALM", "dev-portal")
    KEYCLOAK_CLIENT_ID = os.getenv("KEYCLOAK_CLIENT_ID", "dev-portal")
    KEYCLOAK_CLIENT_SECRET = os.getenv("KEYCLOAK_CLIENT_SECRET", "")
    KEYCLOAK_REDIRECT_URI = os.getenv("KEYCLOAK_REDIRECT_URI", "http://localhost:8000/auth/callback")
    # Browser origin of this portal, no trailing slash. Logout uses this
    # instead of the incoming Host header.
    PORTAL_PUBLIC_URL = os.getenv("PORTAL_PUBLIC_URL", "http://localhost:8000").rstrip("/")

    # Only members of this Keycloak group are granted a session; set to "" to disable the check.
    KEYCLOAK_REQUIRED_GROUP = os.getenv("KEYCLOAK_REQUIRED_GROUP", "dev-portal-users")

    # Signs the session cookie; set a strong random value in real deployments
    SESSION_SECRET_KEY = os.getenv("SESSION_SECRET_KEY", "dev-session-secret-change-me")
    # Set to "true" once served over HTTPS so the session cookie requires TLS.
    SESSION_COOKIE_SECURE = os.getenv("SESSION_COOKIE_SECURE", "false").lower() == "true"
    # Short-lived by default: this tool can trigger prod deployments, and group
    # membership is only re-checked at login, so sessions shouldn't outlive a workday.
    SESSION_MAX_AGE_SECONDS = int(os.getenv("SESSION_MAX_AGE_SECONDS", str(8 * 60 * 60)))

    # Local ledger of pipeline triggers. Overridden to /data/releases.db in compose
    # so the file lives on a volume the container can rewrite across restarts.
    RELEASES_DB_PATH = os.getenv("RELEASES_DB_PATH", os.path.join(_APP_DIR, "data", "releases.db"))


_DEFAULT_SESSION_SECRET = "dev-session-secret-change-me"


def assert_runtime_config() -> None:
    """Fail closed before the app serves traffic with a placeholder secret."""
    secret = Config.SESSION_SECRET_KEY
    if len(secret) < 16:
        raise RuntimeError("SESSION_SECRET_KEY must be at least 16 characters.")
    if Config.SESSION_COOKIE_SECURE and secret == _DEFAULT_SESSION_SECRET:
        raise RuntimeError(
            "SESSION_SECRET_KEY is still the local default. Set a unique secret before serving over HTTPS."
        )
    if not Config.KEYCLOAK_CLIENT_SECRET:
        raise RuntimeError("KEYCLOAK_CLIENT_SECRET is required.")
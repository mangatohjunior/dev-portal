import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from config import Config
from logging_utils import audit_log, logger

router = APIRouter()

# Paths reachable without an authenticated session.
PUBLIC_PATHS = {"/login", "/login/keycloak", "/auth/callback", "/logout"}

# Contextual notices shown on the landing page, selected via ?reason=...
_REASON_MESSAGES = {
    "unauthorized": ("border-amber-200 bg-amber-50 text-amber-900", "Please sign in to continue."),
    "logout": ("border-stone-200 bg-stone-50 text-stone-600", "You have been logged out."),
    "forbidden": (
        "border-red-200 bg-red-50 text-red-800",
        "Your account isn't authorized to access this application. Contact an administrator if you believe this is a mistake.",
    ),
    "failed": ("border-red-200 bg-red-50 text-red-800", "Sign-in failed. Please try again."),
}


def _landing_page(reason: str | None) -> str:
    notice = ""
    if reason in _REASON_MESSAGES:
        classes, text = _REASON_MESSAGES[reason]
        notice = f'<div class="mb-6 rounded-lg border px-3 py-2.5 text-sm {classes}">{text}</div>'
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Sign in · Dev portal</title>
    <script src="https://cdn.tailwindcss.com"></script>
</head>
<body class="min-h-screen bg-[#f4f3ef] text-stone-900 antialiased">
    <div class="min-h-screen lg:grid lg:grid-cols-2">
        <section class="hidden lg:flex flex-col justify-between bg-[#0f2744] p-12 text-stone-100">
            <div class="flex items-center gap-2.5">
                <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true"><rect width="28" height="28" rx="8" fill="#1d4ed8"/><path d="M11 9l7 5-7 5" fill="none" stroke="#fafaf9" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
                <span class="text-[15px] font-semibold tracking-tight">Dev portal</span>
            </div>
            <div class="max-w-md">
                <h1 class="text-4xl font-semibold tracking-tight leading-[1.15]">Release a tenant with a record of who shipped it.</h1>
                <p class="mt-5 text-sm leading-relaxed text-stone-400">Trigger the pipeline, attach the change request for production, and read the history from the same place.</p>
            </div>
            <p class="text-xs tracking-wide text-stone-500">Company account via Keycloak</p>
        </section>
        <section class="flex min-h-screen items-center justify-center px-6 py-16">
            <div class="w-full max-w-sm">
                <div class="mb-8 flex items-center gap-2.5 lg:hidden">
                    <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true"><rect width="28" height="28" rx="8" fill="#1d4ed8"/><path d="M11 9l7 5-7 5" fill="none" stroke="#fafaf9" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>
                    <span class="text-[15px] font-semibold tracking-tight">Dev portal</span>
                </div>
                <h2 class="text-2xl font-semibold tracking-tight">Sign in</h2>
                <p class="mt-2 text-sm leading-relaxed text-stone-600">Use your company account to open the release console.</p>
                <div class="mt-8">
                    {notice}
                    <a href="/login/keycloak" class="inline-flex w-full items-center justify-center rounded-lg bg-blue-700 px-4 py-3 text-sm font-medium text-white hover:bg-blue-800">
                        Continue with Keycloak
                    </a>
                </div>
            </div>
        </section>
    </div>
</body>
</html>"""


def _session_user(user_info: dict) -> dict:
    """Keep only the claims the portal displays. The cookie is signed, not encrypted."""
    raw_name = user_info.get("preferred_username")
    username = raw_name if isinstance(raw_name, str) else ""
    username = "".join(ch for ch in username if ch.isprintable() and ch not in "\r\n\"\\")[:128]
    raw_email = user_info.get("email")
    email = raw_email if isinstance(raw_email, str) else ""
    if any(ch in email for ch in "\r\n") or not email.isprintable():
        email = ""
    return {"preferred_username": username or "unknown", "email": email[:254]}


def _authorize_url(state: str) -> str:
    params = {
        "client_id": Config.KEYCLOAK_CLIENT_ID,
        "response_type": "code",
        "redirect_uri": Config.KEYCLOAK_REDIRECT_URI,
        "scope": "openid profile email",
        "state": state,
    }
    # Browser-facing endpoint: must use the externally reachable Keycloak URL.
    base = f"{Config.KEYCLOAK_EXTERNAL_URL}/realms/{Config.KEYCLOAK_REALM}/protocol/openid-connect/auth"
    return f"{base}?{urlencode(params)}"


@router.get("/login")
async def login_page(reason: str | None = None):
    return HTMLResponse(_landing_page(reason))


@router.get("/login/keycloak")
async def login_start(request: Request):
    state = secrets.token_urlsafe(16)
    request.session["oauth_state"] = state
    return RedirectResponse(_authorize_url(state))


@router.get("/auth/callback")
async def auth_callback(request: Request, code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return RedirectResponse("/login?reason=failed")
    if not code or not state or state != request.session.pop("oauth_state", None):
        return RedirectResponse("/login?reason=failed")

    # Server-to-server calls use the internal (container network) Keycloak URL.
    issuer = f"{Config.KEYCLOAK_INTERNAL_URL}/realms/{Config.KEYCLOAK_REALM}/protocol/openid-connect"
    async with httpx.AsyncClient(timeout=20, follow_redirects=False) as client:
        token_resp = await client.post(
            f"{issuer}/token",
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": Config.KEYCLOAK_REDIRECT_URI,
                "client_id": Config.KEYCLOAK_CLIENT_ID,
                "client_secret": Config.KEYCLOAK_CLIENT_SECRET,
            },
        )
        if token_resp.status_code != 200:
            logger.warning("Keycloak token exchange failed: status %s", token_resp.status_code)
            return RedirectResponse("/login?reason=failed")

        try:
            access_token = token_resp.json().get("access_token")
        except ValueError:
            return RedirectResponse("/login?reason=failed")
        if not isinstance(access_token, str) or not access_token:
            logger.warning("Keycloak token exchange returned no access token")
            return RedirectResponse("/login?reason=failed")

        userinfo_resp = await client.get(
            f"{issuer}/userinfo",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        if userinfo_resp.status_code != 200:
            logger.warning("Keycloak userinfo call failed: status %s", userinfo_resp.status_code)
            return RedirectResponse("/login?reason=failed")

        try:
            user_info = userinfo_resp.json()
        except ValueError:
            return RedirectResponse("/login?reason=failed")
    if not isinstance(user_info, dict):
        return RedirectResponse("/login?reason=failed")
    username = user_info.get("preferred_username", "unknown")
    if not isinstance(username, str):
        username = "unknown"
    groups = user_info.get("groups", [])
    if isinstance(groups, str):
        groups = [groups]
    if Config.KEYCLOAK_REQUIRED_GROUP and (
        not isinstance(groups, list) or Config.KEYCLOAK_REQUIRED_GROUP not in groups
    ):
        audit_log("login_forbidden", username=username, detail=f"not a member of {Config.KEYCLOAK_REQUIRED_GROUP}")
        return RedirectResponse("/login?reason=forbidden")

    # Tokens are discarded. The cookie keeps the display name and email only.
    request.session.clear()
    request.session["user"] = _session_user(user_info)
    audit_log("login_success", username=request.session["user"]["preferred_username"])
    return RedirectResponse("/")


@router.get("/logout")
async def logout(request: Request):
    username = (request.session.get("user") or {}).get("preferred_username", "unknown")
    request.session.clear()
    audit_log("logout", username=username)
    # Also end the Keycloak SSO session, not just our app's cookie.
    params = {
        "client_id": Config.KEYCLOAK_CLIENT_ID,
        "post_logout_redirect_uri": f"{Config.PORTAL_PUBLIC_URL}/login?reason=logout",
    }
    end_session_url = f"{Config.KEYCLOAK_EXTERNAL_URL}/realms/{Config.KEYCLOAK_REALM}/protocol/openid-connect/logout?{urlencode(params)}"
    return RedirectResponse(end_session_url)


@router.get("/api/me")
async def me(request: Request):
    user = request.session.get("user")
    if not user:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return {"preferred_username": user.get("preferred_username"), "email": user.get("email")}

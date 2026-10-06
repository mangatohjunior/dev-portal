"""Auth gate, security headers, and the session cookie."""

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, RedirectResponse
from starlette.middleware.sessions import SessionMiddleware

from auth import PUBLIC_PATHS, router as auth_router
from config import Config


def _same_origin(request: Request) -> bool:
    origin = request.headers.get("origin")
    if not origin:
        return True
    allowed = {Config.PORTAL_PUBLIC_URL, str(request.base_url).rstrip("/")}
    return origin.rstrip("/") in allowed


def install_middleware(app: FastAPI) -> None:
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
    # early-return redirects and 401s too, not just responses from route handlers.
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

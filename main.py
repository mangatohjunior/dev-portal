"""Dev portal entrypoint. The pieces live in the app package."""

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.middleware import install_middleware
from app.pipelines import router as pipelines_router
from app.releases import router as releases_router
from app.startup import lifespan
from app.trigger import router as trigger_router

app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

install_middleware(app)
app.include_router(trigger_router)
app.include_router(pipelines_router)
app.include_router(releases_router)

app.mount("/", StaticFiles(directory="public", html=True), name="public")

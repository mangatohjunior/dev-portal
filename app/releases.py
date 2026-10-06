"""Release history: the chart, the production ledger, and the CSV download."""

from fastapi import APIRouter
from fastapi.responses import FileResponse, JSONResponse, Response

from releases_store import list_prod_audit, list_releases, prod_audit_csv

router = APIRouter()

_NO_STORE = {"Cache-Control": "no-store"}


@router.get("/api/releases")
async def get_releases():
    return JSONResponse({"releases": list_releases()}, headers=_NO_STORE)


@router.get("/api/releases/audit")
async def get_prod_audit():
    return JSONResponse({"releases": list_prod_audit()}, headers=_NO_STORE)


@router.get("/api/releases/audit.csv")
async def download_prod_audit():
    return Response(
        content=prod_audit_csv(),
        media_type="text/csv; charset=utf-8",
        headers={
            **_NO_STORE,
            "Content-Disposition": 'attachment; filename="prod-releases.csv"',
        },
    )


@router.get("/releases")
async def releases_page():
    return FileResponse("public/releases.html")

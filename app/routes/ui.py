from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse, RedirectResponse, Response

from app.services.auth import validate_access_token

router = APIRouter(include_in_schema=False)

_BASE_DIR = Path(__file__).resolve().parent.parent
_TEMPLATES_DIR = _BASE_DIR / "templates"


@router.get("/")
async def login_page(request: Request) -> Response:
    token = request.cookies.get("access_token", "").strip()
    if token and await validate_access_token(token):
        return RedirectResponse(url="/dashboard", status_code=303)
    return FileResponse(_TEMPLATES_DIR / "login.html")


@router.get("/dashboard")
async def dashboard_page(request: Request) -> Response:
    token = request.cookies.get("access_token", "").strip()
    if not token:
        return RedirectResponse(url="/", status_code=303)

    user = await validate_access_token(token)
    if not user:
        return RedirectResponse(url="/", status_code=303)

    return FileResponse(_TEMPLATES_DIR / "dashboard.html")

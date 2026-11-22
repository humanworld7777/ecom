from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse

router = APIRouter()

PAGE = Path(__file__).parent / "index.html"


@router.get("/shop", response_class=HTMLResponse, include_in_schema=False)
def storefront():
    try:
        html = PAGE.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HTTPException(
            status_code=500, detail="index.html is missing from the app folder"
        )
    return HTMLResponse(
        html,
        headers={
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
        },
    )

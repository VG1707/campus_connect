"""
CampusConnect - Main FastAPI Application.
FastAPI + Jinja2 + HTMX + Tailwind CSS (via CDN).
No Node.js, npm, or frontend build tool required.
"""

from pathlib import Path
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.templating import Jinja2Templates

from app.database import init_db
from app.routers import common, student, faculty, admin, demo

BASE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = BASE_DIR.parent


def cleanup_temp_uploads() -> None:
    """Remove any orphaned temporary upload files on server startup."""
    temp_dir = PROJECT_ROOT / "temp_uploads"
    if temp_dir.exists() and temp_dir.is_dir():
        for f in temp_dir.iterdir():
            if f.is_file():
                try:
                    f.unlink(missing_ok=True)
                except Exception:
                    pass


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Initialize database tables, auto-seed demo accounts on first run,
    and clean up temporary upload artifacts on startup.
    """
    cleanup_temp_uploads()
    init_db(auto_seed=True)
    yield


app = FastAPI(
    title="CampusConnect",
    description="Deterministic College Faculty Consultation Prototype",
    version="1.0.0",
    lifespan=lifespan
)


@app.get("/health", tags=["system"])
@app.get("/healthz", tags=["system"])
def health_check():
    """Health check endpoint for Render, container orchestrators, and uptime monitors."""
    return {
        "status": "healthy",
        "service": "CampusConnect",
        "version": "1.0.0"
    }


# Jinja2 template setup
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# Include all route groups
app.include_router(common.router)
app.include_router(student.router)
app.include_router(faculty.router)
app.include_router(admin.router)
app.include_router(demo.router)


if __name__ == "__main__":
    import os
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    host = os.environ.get("HOST", "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1")
    uvicorn.run("app.main:app", host=host, port=port, reload=False)

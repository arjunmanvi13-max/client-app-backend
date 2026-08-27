"""PWS & ALPHA Tracker — FastAPI app entrypoint.

Routes live in /app/backend/routers/, shared deps in /app/backend/core.py,
seed logic in /app/backend/seed.py.
"""
import asyncio
import os
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI, Request
from starlette.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pymongo.errors import DuplicateKeyError

from core import client, db, logger
from seed import seed_data
from routers import auth, users, people, tasks, attendance, hostel, notifications, dashboard, coach, command, permissions, fees, uploads, deactivation, parents, alpha_dashboard, reports, academic, invoices, marks, report_cards, coach_assessments, fee_catalog, approvals, pws_fees, academy_structure, expenses, timetable, factory_reset

app = FastAPI(title="PWS & ALPHA Tracker")
api = APIRouter(prefix="/api")

# Mount sub-routers
api.include_router(auth.router)
api.include_router(users.router)
api.include_router(people.router)
api.include_router(tasks.router)
api.include_router(attendance.router)
api.include_router(hostel.router)
api.include_router(notifications.router)
api.include_router(dashboard.router)
api.include_router(coach.router)
api.include_router(command.router)
api.include_router(permissions.router)
api.include_router(fees.router)
api.include_router(pws_fees.router)
api.include_router(uploads.router)
api.include_router(deactivation.router)
api.include_router(approvals.router)
api.include_router(parents.router)
api.include_router(alpha_dashboard.router)
api.include_router(reports.router)
api.include_router(academic.router)
api.include_router(factory_reset.router)
api.include_router(invoices.router)
api.include_router(marks.router)
api.include_router(report_cards.router)
api.include_router(coach_assessments.router)
api.include_router(fee_catalog.router)
api.include_router(academy_structure.router)
api.include_router(expenses.router)
api.include_router(timetable.router)

@api.get("/")
async def root():
    return {"app": "PWS & ALPHA Tracker", "status": "ok"}


@api.get("/health")
async def health():
    """Lightweight liveness probe — no DB access."""
    return {"status": "ok"}


@api.get("/ready")
async def ready():
    """Readiness probe — verifies the database is actually reachable.

    /health returns ok even when Mongo is down, so the platform would keep
    routing traffic to a process that cannot serve a single real request.
    """
    try:
        await client.admin.command("ping")
    except Exception as exc:
        logger.warning("Readiness check failed: %s", exc)
        return JSONResponse({"status": "degraded", "database": "unreachable"}, status_code=503)
    return {"status": "ok", "database": "ok"}

app.include_router(api)

ALLOWED_ORIGINS = [
    o.strip() for o in (os.getenv("CORS_ALLOWED_ORIGINS") or "").split(",") if o.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS or ["*"],
    allow_credentials=bool(ALLOWED_ORIGINS),
    allow_methods=["*"],
    allow_headers=["*"],
)

if not ALLOWED_ORIGINS:
    logger.warning(
        "CORS_ALLOWED_ORIGINS is not set — falling back to '*'. Set it to the "
        "frontend origin(s) in production."
    )


@app.middleware("http")
async def security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault(
        "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
    )
    return response


@app.on_event("startup")
async def on_start():
    """Run idempotent seed on boot without blocking login or health checks."""
    asyncio.create_task(_run_startup_seed())


async def _run_startup_seed():
    """Failures must never prevent the API from serving."""
    try:
        await seed_data()
        logger.info("Seed completed")
    except DuplicateKeyError as exc:
        logger.warning("Seed duplicate key on startup (continuing): %s", exc)
    except Exception as exc:
        logger.exception("Seed failed on startup (continuing): %s", exc)

@app.on_event("shutdown")
async def on_stop():
    client.close()

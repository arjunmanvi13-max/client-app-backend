"""PWS & ALPHA Tracker — FastAPI app entrypoint.

Routes live in /app/backend/routers/, shared deps in /app/backend/core.py,
seed logic in /app/backend/seed.py.
"""
from fastapi import APIRouter, FastAPI
from starlette.middleware.cors import CORSMiddleware
from pymongo.errors import DuplicateKeyError

from core import client, logger
from seed import seed_data
from routers import auth, users, people, tasks, attendance, hostel, notifications, dashboard, coach, command, permissions, fees, uploads, deactivation, parents, alpha_dashboard, reports, academic, invoices, marks, report_cards, coach_assessments, fee_catalog, approvals

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
api.include_router(uploads.router)
api.include_router(deactivation.router)
api.include_router(approvals.router)
api.include_router(parents.router)
api.include_router(alpha_dashboard.router)
api.include_router(reports.router)
api.include_router(academic.router)
api.include_router(invoices.router)
api.include_router(marks.router)
api.include_router(report_cards.router)
api.include_router(coach_assessments.router)
api.include_router(fee_catalog.router)

@api.get("/")
async def root():
    return {"app": "PWS & ALPHA Tracker", "status": "ok"}

app.include_router(api)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.on_event("startup")
async def on_start():
    """Run idempotent seed on boot. Failures must never prevent the API from serving."""
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

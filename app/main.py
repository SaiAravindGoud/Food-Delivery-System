
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse

load_dotenv()

from app.auth.dependencies import get_current_user
from app.database import engine
from app.jobs.auto_reject_orders import auto_reject_pending_orders
from app.jobs.coupon_expiry_job import deactivate_expired_coupons
from app.jobs.delivery_assignment_job import (
    retry_pending_delivery_assignments,
)
from app.jobs.inactive_delivery_partner_job import (
    mark_inactive_partners_offline,
)
from app.models.entities import User
from app.routers.auth import router as auth_router
from app.routers.restaurants import router as restaurants_router
from app.routers.menu import router as menu_router
from app.routers.cart import router as cart_router
from app.routers.addresses import router as addresses_router
from app.routers.orders import router as orders_router
from app.routers.owner_orders import router as owner_orders_router
from app.routers.restaurant_orders import router as restaurant_orders_router
from app.routers.delivery import router as delivery_router
from app.routers import coupons
from app.websockets.routes import router as websocket_router
from app.routers.reviews import router as reviews_router
from app.routers.reports import router as reports_router

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(
        retry_pending_delivery_assignments,
        trigger="interval",
        seconds=10,
        id="retry_pending_delivery_assignments",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        auto_reject_pending_orders,
        trigger="interval",
        minutes=1,
        id="auto_reject_pending_orders",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        deactivate_expired_coupons,
        trigger="interval",
        days=1,
        id="deactivate_expired_coupons",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.add_job(
        mark_inactive_partners_offline,
        trigger="interval",
        minutes=5,
        id="inactive_delivery_partner_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    scheduler.start()

    logger.info("Delivery offer expiry/retry scheduler started (10 seconds)")
    logger.info("Pending-order auto-rejection scheduler started (1 minute)")
    logger.info("Daily coupon expiry scheduler started")
    logger.info("Inactive delivery partner scheduler started (5 minutes)")

    try:
        yield
    finally:
        if scheduler.running:
            scheduler.shutdown(wait=False)

        await engine.dispose()
        logger.info("Application shutdown complete")


app = FastAPI(
    title="Food Delivery System API",
    version="1.0.0",
    lifespan=lifespan,
)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request,
    exc: RequestValidationError,
):
    errors = [
        {
            "field": ".".join(str(part) for part in error["loc"]),
            "message": error["msg"],
            "type": error["type"],
        }
        for error in exc.errors()
    ]

    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": {
                "code": "VALIDATION_ERROR",
                "message": "Request validation failed",
                "details": errors,
            },
        },
    )


@app.exception_handler(HTTPException)
async def http_exception_handler(
    request: Request,
    exc: HTTPException,
):
    return JSONResponse(
        status_code=exc.status_code,
        headers=exc.headers,
        content={
            "success": False,
            "error": {
                "code": "HTTP_ERROR",
                "message": str(exc.detail),
            },
        },
    )


@app.exception_handler(Exception)
async def unexpected_exception_handler(
    request: Request,
    exc: Exception,
):
    logger.error(
        "Unhandled exception while processing %s %s",
        request.method,
        request.url.path,
        exc_info=(type(exc), exc, exc.__traceback__),
    )

    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {
                "code": "INTERNAL_SERVER_ERROR",
                "message": "An unexpected server error occurred",
            },
        },
    )


app.include_router(auth_router)
app.include_router(restaurants_router)
app.include_router(menu_router)
app.include_router(cart_router)
app.include_router(addresses_router)
app.include_router(orders_router)
app.include_router(owner_orders_router)
app.include_router(restaurant_orders_router)
app.include_router(delivery_router)
app.include_router(coupons.router)
app.include_router(websocket_router)
app.include_router(reviews_router)
app.include_router(reports_router)


@app.get("/health", tags=["Health"])
async def health_check():
    return {"status": "healthy"}


@app.get("/auth/me", tags=["Authentication"])
async def get_my_account(
    current_user: User = Depends(get_current_user),
):
    return {
        "id": current_user.id,
        "full_name": current_user.full_name,
        "email": current_user.email,
        "role": current_user.role,
    }


@app.get("/tracking-test", include_in_schema=False)
async def tracking_test_page():
    page_path = (
        Path(__file__).resolve().parent.parent
        / "static"
        / "tracking_test.html"
    )

    if not page_path.is_file():
        raise HTTPException(
            status_code=404,
            detail=(
                "Tracking test page not found. "
                "Check static/tracking_test.html."
            ),
        )

    return FileResponse(
        path=page_path,
        media_type="text/html",
    )

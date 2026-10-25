
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.entities import Coupon

logger = logging.getLogger(__name__)


async def deactivate_expired_coupons() -> None:
    """Deactivate coupons whose expiry time has passed."""
    now = datetime.now(timezone.utc)

    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(Coupon)
                .where(
                    Coupon.is_active.is_(True),
                    Coupon.expires_at.is_not(None),
                    Coupon.expires_at <= now,
                )
                .with_for_update()
            )

            expired_coupons = result.scalars().all()

            for coupon in expired_coupons:
                coupon.is_active = False

            await db.commit()

            if expired_coupons:
                logger.info(
                    "Deactivated %s expired coupon(s).",
                    len(expired_coupons),
                )

        except Exception:
            await db.rollback()
            logger.exception("Coupon expiry job failed.")

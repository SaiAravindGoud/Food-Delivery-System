
import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.entities import DeliveryPartner

logger = logging.getLogger(__name__)


async def mark_inactive_partners_offline() -> None:
    """Mark partners unavailable when their location is stale."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=10)

    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(DeliveryPartner).where(
                    DeliveryPartner.is_active.is_(True),
                    DeliveryPartner.is_available.is_(True),
                    DeliveryPartner.last_location_update.is_not(None),
                    DeliveryPartner.last_location_update < cutoff,
                )
            )

            partners = result.scalars().all()

            for partner in partners:
                partner.is_available = False

            if partners:
                await db.commit()
                logger.info(
                    "Marked %s inactive delivery partners unavailable",
                    len(partners),
                )
            else:
                logger.info(
                    "No inactive delivery partners needed updating"
                )

    except Exception:
        logger.exception(
            "Failed to mark inactive delivery partners unavailable"
        )

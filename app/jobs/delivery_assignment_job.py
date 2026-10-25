
import logging
from datetime import datetime, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.entities import (
    AdminAlert,
    Delivery,
    DeliveryOffer,
    Order,
    OrderStatusHistory,
)
from app.services.delivery_assignment import (
    offer_delivery_to_next_partner,
)
from app.websockets.manager import manager
from app.websockets.routes import load_order_tracking

logger = logging.getLogger(__name__)

ELIGIBLE_ORDER_STATUSES = ("Accepted", "Ready for Pickup")
ADMIN_ALERT_DELAY_MINUTES = 15


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def make_admin_alert_message(order_id: int, order_number: str) -> str:
    return (
        f"Order {order_number} (ID: {order_id}) has been waiting "
        f"for a delivery partner for at least 15 minutes."
    )


async def create_unassigned_order_alert(
    order_id: int,
    now: datetime,
) -> None:
    """
    Persist and broadcast one Admin alert when an accepted order has
    remained unassigned for at least 15 minutes.
    """
    alert_key = f"unassigned_delivery_order_{order_id}"
    alert_data = None

    async with AsyncSessionLocal() as db:
        try:
            order = await db.scalar(
                select(Order).where(Order.id == order_id)
            )

            if (
                order is None
                or order.status not in ELIGIBLE_ORDER_STATUSES
            ):
                return

            delivery = await db.scalar(
                select(Delivery).where(
                    Delivery.order_id == order_id
                )
            )

            if (
                delivery is None
                or delivery.status != "Pending"
                or delivery.delivery_partner_id is not None
            ):
                return

            # Use the first Accepted status-history timestamp.
            accepted_at = await db.scalar(
                select(OrderStatusHistory.created_at)
                .where(
                    OrderStatusHistory.order_id == order_id,
                    OrderStatusHistory.new_status == "Accepted",
                )
                .order_by(OrderStatusHistory.created_at.asc())
                .limit(1)
            )

            if accepted_at is None:
                # Do not guess the acceptance time if history is missing.
                return

            # SQLAlchemy DateTime columns may return naive UTC datetimes.
            if accepted_at.tzinfo is None:
                accepted_at = accepted_at.replace(tzinfo=timezone.utc)

            elapsed_seconds = (now - accepted_at).total_seconds()
            required_seconds = ADMIN_ALERT_DELAY_MINUTES * 60

            if elapsed_seconds < required_seconds:
                return

            # A stable unique key prevents repeat alerts on every job run.
            existing_alert = await db.scalar(
                select(AdminAlert.id).where(
                    AdminAlert.alert_key == alert_key
                )
            )

            if existing_alert is not None:
                return

            order_number = str(
                getattr(order, "order_number", order_id)
            )

            alert = AdminAlert(
                alert_key=alert_key,
                order_id=order_id,
                alert_type="unassigned_delivery",
                message=make_admin_alert_message(
                    order_id,
                    order_number,
                ),
            )

            db.add(alert)
            await db.flush()
            await db.refresh(alert)

            # Copy values before closing the database session.
            alert_data = {
                "type": "admin_alert",
                "alert_id": alert.id,
                "alert_key": alert.alert_key,
                "order_id": alert.order_id,
                "alert_type": alert.alert_type,
                "message": alert.message,
                "created_at": (
                    alert.created_at.isoformat()
                    if alert.created_at
                    else now.isoformat()
                ),
            }

            await db.commit()

        except Exception:
            await db.rollback()
            logger.exception(
                "Could not create Admin alert for order %s",
                order_id,
            )
            return

    if alert_data is not None:
        try:
            await manager.broadcast_admins(alert_data)
            logger.warning(
                "Admin alert created for unassigned order %s",
                order_id,
            )
        except Exception:
            # The alert remains saved even if the broadcast fails.
            logger.exception(
                "Admin alert for order %s was saved, "
                "but broadcasting failed",
                order_id,
            )


async def retry_pending_delivery_assignments() -> None:
    """
    Expire stale offers, offer eligible orders to the next partner,
    and alert Admins about orders unassigned for at least 15 minutes.
    """
    now = utc_now()
    expired_order_ids = set()

    # 1. Expire offers whose response window has elapsed.
    async with AsyncSessionLocal() as db:
        try:
            pending_offers = await db.scalars(
                select(DeliveryOffer).where(
                    DeliveryOffer.status == "Pending",
                    DeliveryOffer.expires_at <= now,
                )
            )

            for offer in pending_offers:
                offer.status = "Expired"
                offer.responded_at = now
                expired_order_ids.add(offer.order_id)

            await db.commit()

        except Exception:
            await db.rollback()
            logger.exception("Could not expire stale delivery offers")
            return

    # 2. Find accepted orders that still need a delivery partner.
    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(Order.id)
                .join(Delivery, Delivery.order_id == Order.id)
                .where(
                    Order.status.in_(ELIGIBLE_ORDER_STATUSES),
                    Delivery.status == "Pending",
                    Delivery.delivery_partner_id.is_(None),
                )
                .order_by(Order.placed_at.asc())
            )

            order_ids = result.scalars().all()

        except Exception:
            await db.rollback()
            logger.exception(
                "Could not find orders waiting for delivery offers"
            )
            return

    # 3. Offer each eligible order to the next available partner.
    for order_id in order_ids:
        offer_id = None
        partner_id = None

        async with AsyncSessionLocal() as db:
            try:
                order = await db.scalar(
                    select(Order).where(Order.id == order_id)
                )

                if (
                    order is None
                    or order.status not in ELIGIBLE_ORDER_STATUSES
                ):
                    continue

                delivery = await db.scalar(
                    select(Delivery)
                    .where(Delivery.order_id == order_id)
                    .with_for_update()
                )

                if (
                    delivery is None
                    or delivery.status != "Pending"
                    or delivery.delivery_partner_id is not None
                ):
                    continue

                offer = await offer_delivery_to_next_partner(
                    db,
                    order,
                )

                if offer is not None:
                    offer_id = offer.id
                    partner_id = offer.delivery_partner_id

                await db.commit()

            except Exception:
                await db.rollback()
                logger.exception(
                    "Creating delivery offer failed for order %s",
                    order_id,
                )
                continue

        if offer_id is not None:
            try:
                await manager.broadcast_partners(
                    {
                        "type": "delivery_offer",
                        "offer_id": offer_id,
                        "order_id": order_id,
                        "delivery_partner_id": partner_id,
                        "expires_in_seconds": 30,
                    }
                )

                logger.info(
                    "Created offer %s for partner %s, order %s",
                    offer_id,
                    partner_id,
                    order_id,
                )

            except Exception:
                logger.exception(
                    "Offer %s was saved, but broadcasting failed",
                    offer_id,
                )
        else:
            logger.debug(
                "No eligible partner available for order %s",
                order_id,
            )

    # 4. Check the 15-minute threshold, including orders that already
    # have a pending offer but have not yet been assigned a partner.
    for order_id in order_ids:
        await create_unassigned_order_alert(order_id, now)

    # 5. Refresh tracking for orders whose offers expired.
    for order_id in expired_order_ids:
        try:
            tracking_data = await load_order_tracking(order_id)
            await manager.broadcast_order(order_id, tracking_data)

        except Exception:
            logger.exception(
                "Could not refresh tracking after offer expiry "
                "for order %s",
                order_id,
            )


import logging
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.database import AsyncSessionLocal
from app.models.entities import (
    Coupon,
    Order,
    OrderStatusHistory,
    Payment,
)
from app.websockets.manager import manager
from app.websockets.routes import load_order_tracking

logger = logging.getLogger(__name__)

AUTO_REJECT_MINUTES = 5
AUTO_REJECT_REASON = "Restaurant did not respond."


async def auto_reject_pending_orders() -> None:
    """
    Reject orders that remain Pending or Placed for five minutes.
    Refund paid online payments, release coupon usage, record history,
    and notify connected order-tracking clients.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=AUTO_REJECT_MINUTES)
    rejected_order_ids: list[int] = []

    async with AsyncSessionLocal() as db:
        try:
            result = await db.execute(
                select(Order)
                .where(
                    Order.status.in_(("Pending", "Placed")),
                    Order.placed_at <= cutoff,
                )
                .with_for_update()
            )
            orders = result.scalars().all()

            for order in orders:
                # Recheck the status before changing it.
                if order.status not in {"Pending", "Placed"}:
                    continue

                old_status = order.status
                order.status = "Rejected"

                db.add(
                    OrderStatusHistory(
                        order_id=order.id,
                        old_status=old_status,
                        new_status="Rejected",
                        changed_by_user_id=None,
                        notes=f"System: {AUTO_REJECT_REASON}",
                    )
                )

                payment_result = await db.execute(
                    select(Payment)
                    .where(Payment.order_id == order.id)
                    .with_for_update()
                )
                payment = payment_result.scalar_one_or_none()

                if (
                    order.payment_method.strip().upper() == "ONLINE"
                    and payment is not None
                    and payment.status == "Paid"
                ):
                    payment.status = "Refunded"
                    order.payment_status = "Refunded"

                if order.coupon_id is not None:
                    coupon_result = await db.execute(
                        select(Coupon)
                        .where(Coupon.id == order.coupon_id)
                        .with_for_update()
                    )
                    coupon = coupon_result.scalar_one_or_none()

                    if coupon is not None and coupon.used_count > 0:
                        coupon.used_count -= 1

                rejected_order_ids.append(order.id)

            await db.commit()

        except Exception:
            await db.rollback()
            logger.exception("Automatic order rejection failed.")
            return

    # Notify connected clients only after the database transaction commits.
    for order_id in rejected_order_ids:
        try:
            await manager.broadcast_order(
                order_id,
                {
                    "type": "order_rejected",
                    "order_id": order_id,
                    "status": "Rejected",
                    "reason": AUTO_REJECT_REASON,
                    "message": "Your order was rejected automatically "
                    "because the restaurant did not respond within "
                    "five minutes.",
                },
            )

            tracking_data = await load_order_tracking(order_id)
            if tracking_data is not None:
                await manager.broadcast_order(order_id, tracking_data)

        except Exception:
            logger.exception(
                "Could not notify tracking clients for rejected order %s",
                order_id,
            )

    if rejected_order_ids:
        logger.info(
            "Automatically rejected %s order(s): %s",
            len(rejected_order_ids),
            rejected_order_ids,
        )

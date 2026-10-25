
import math
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import (
    Delivery,
    DeliveryOffer,
    DeliveryPartner,
    Order,
    Restaurant,
)


ACTIVE_DELIVERY_STATUSES = {"Assigned", "Picked Up", "On the Way"}
PENDING_OFFER_SECONDS = 30


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""
    return datetime.now(timezone.utc)


def distance_km(
    latitude1: float,
    longitude1: float,
    latitude2: float,
    longitude2: float,
) -> float:
    """Calculate the great-circle distance between two coordinates."""
    earth_radius_km = 6371.0

    lat1 = math.radians(latitude1)
    lat2 = math.radians(latitude2)
    delta_lat = math.radians(latitude2 - latitude1)
    delta_lon = math.radians(longitude2 - longitude1)

    value = (
        math.sin(delta_lat / 2) ** 2
        + math.cos(lat1)
        * math.cos(lat2)
        * math.sin(delta_lon / 2) ** 2
    )
    value = min(1.0, max(0.0, value))

    return earth_radius_km * 2 * math.atan2(
        math.sqrt(value),
        math.sqrt(1.0 - value),
    )


async def get_or_create_delivery(
    db: AsyncSession,
    order: Order,
) -> Delivery:
    """Return the order's delivery record, creating it if necessary."""
    delivery = await db.scalar(
        select(Delivery)
        .where(Delivery.order_id == order.id)
        .with_for_update()
    )

    if delivery is None:
        delivery = Delivery(
            order_id=order.id,
            status="Pending",
        )
        db.add(delivery)
        await db.flush()

    return delivery


async def get_eligible_partners(
    db: AsyncSession,
    order: Order,
) -> list[tuple[float, DeliveryPartner]]:
    """Return eligible delivery partners ordered by distance."""
    restaurant = await db.get(Restaurant, order.restaurant_id)

    if restaurant is None:
        raise ValueError("Restaurant not found for this order")

    if (
        restaurant.latitude is None
        or restaurant.longitude is None
    ):
        raise ValueError(
            "Restaurant coordinates are required for delivery assignment"
        )

    active_delivery_exists = (
        select(Delivery.id)
        .where(
            Delivery.delivery_partner_id == DeliveryPartner.id,
            Delivery.status.in_(ACTIVE_DELIVERY_STATUSES),
        )
        .exists()
    )

    pending_offer_exists = (
        select(DeliveryOffer.id)
        .where(
            DeliveryOffer.delivery_partner_id == DeliveryPartner.id,
            DeliveryOffer.status == "Pending",
            DeliveryOffer.expires_at > utc_now(),
        )
        .exists()
    )

    result = await db.execute(
        select(DeliveryPartner).where(
            DeliveryPartner.is_active.is_(True),
            DeliveryPartner.is_available.is_(True),
            DeliveryPartner.current_latitude.is_not(None),
            DeliveryPartner.current_longitude.is_not(None),
            ~active_delivery_exists,
            ~pending_offer_exists,
        )
    )

    partners = result.scalars().all()

    max_distance = min(
        5.0,
        float(restaurant.delivery_radius_km or 5.0),
    )

    eligible: list[tuple[float, DeliveryPartner]] = []

    for partner in partners:
        distance = distance_km(
            float(restaurant.latitude),
            float(restaurant.longitude),
            float(partner.current_latitude),
            float(partner.current_longitude),
        )

        if distance <= max_distance:
            eligible.append((distance, partner))

    eligible.sort(key=lambda item: (item[0], item[1].id))
    return eligible


async def offer_delivery_to_next_partner(
    db: AsyncSession,
    order: Order,
) -> DeliveryOffer | None:
    """
    Offer an eligible order to the nearest available delivery partner.

    Pending offers expire after 30 seconds. Expired or rejected offers
    do not permanently disqualify a partner from future attempts.
    """
    delivery = await get_or_create_delivery(db, order)

    if delivery.delivery_partner_id is not None:
        return None

    if delivery.status != "Pending":
        return None

    now = utc_now()

    # Reuse an existing offer if it is still valid.
    current_offer = await db.scalar(
        select(DeliveryOffer)
        .where(
            DeliveryOffer.order_id == order.id,
            DeliveryOffer.status == "Pending",
            DeliveryOffer.expires_at > now,
        )
        .order_by(DeliveryOffer.id.desc())
        .limit(1)
    )

    if current_offer is not None:
        return current_offer

    # Expire stale pending offers for this order.
    old_offers = await db.scalars(
        select(DeliveryOffer).where(
            DeliveryOffer.order_id == order.id,
            DeliveryOffer.status == "Pending",
        )
    )

    for old_offer in old_offers:
        old_offer.status = "Expired"
        old_offer.responded_at = now

    # Flush status changes before querying eligible partners.
    await db.flush()

    # Previously contacted partners may receive another offer if
    # they remain eligible and their earlier offer has ended.
    eligible_partners = await get_eligible_partners(db, order)

    for _, partner in eligible_partners:
        offer = DeliveryOffer(
            order_id=order.id,
            delivery_partner_id=partner.id,
            status="Pending",
            offered_at=now,
            expires_at=now + timedelta(
                seconds=PENDING_OFFER_SECONDS
            ),
        )

        db.add(offer)
        await db.flush()
        return offer

    return None


async def accept_delivery_offer(
    db: AsyncSession,
    offer_id: int,
    partner: DeliveryPartner,
) -> Delivery:
    """Accept a valid offer and assign its delivery to the partner."""
    now = utc_now()

    offer = await db.scalar(
        select(DeliveryOffer)
        .where(DeliveryOffer.id == offer_id)
        .with_for_update()
    )

    if offer is None:
        raise ValueError("Delivery offer not found")

    if offer.delivery_partner_id != partner.id:
        raise ValueError(
            "This offer does not belong to this delivery partner"
        )

    if offer.status != "Pending":
        raise ValueError("This offer is no longer pending")

    # MySQL may return a naive datetime even when timezone=True.
    expires_at = offer.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= now:
        offer.status = "Expired"
        offer.responded_at = now
        await db.flush()
        raise ValueError("This delivery offer has expired")

    if not partner.is_active or not partner.is_available:
        raise ValueError(
            "Delivery partner is not currently available"
        )

    delivery = await db.scalar(
        select(Delivery)
        .where(Delivery.order_id == offer.order_id)
        .with_for_update()
    )

    if delivery is None:
        raise ValueError("Delivery record not found")

    if delivery.delivery_partner_id is not None:
        raise ValueError("This delivery has already been assigned")

    delivery.delivery_partner_id = partner.id
    delivery.status = "Assigned"
    delivery.pickup_otp = f"{secrets.randbelow(10000):04d}"
    delivery.delivery_otp = f"{secrets.randbelow(10000):04d}"

    partner.is_available = False

    offer.status = "Accepted"
    offer.responded_at = now

    # Invalidate any other pending offers for the same order.
    other_offers = await db.scalars(
        select(DeliveryOffer).where(
            DeliveryOffer.order_id == offer.order_id,
            DeliveryOffer.id != offer.id,
            DeliveryOffer.status == "Pending",
        )
    )

    for other_offer in other_offers:
        other_offer.status = "Expired"
        other_offer.responded_at = now

    await db.flush()
    return delivery


async def reject_delivery_offer(
    db: AsyncSession,
    offer_id: int,
    partner: DeliveryPartner,
) -> DeliveryOffer:
    """Reject a pending offer belonging to this partner."""
    now = utc_now()

    offer = await db.scalar(
        select(DeliveryOffer)
        .where(DeliveryOffer.id == offer_id)
        .with_for_update()
    )

    if offer is None:
        raise ValueError("Delivery offer not found")

    if offer.delivery_partner_id != partner.id:
        raise ValueError(
            "This offer does not belong to this delivery partner"
        )

    if offer.status != "Pending":
        raise ValueError("This offer is no longer pending")

    expires_at = offer.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if expires_at <= now:
        offer.status = "Expired"
        offer.responded_at = now
        await db.flush()
        raise ValueError("This delivery offer has expired")

    offer.status = "Rejected"
    offer.responded_at = now

    await db.flush()
    return offer
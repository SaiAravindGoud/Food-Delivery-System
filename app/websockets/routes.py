
from datetime import datetime, timezone

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.auth.security import decode_access_token
from app.database import AsyncSessionLocal
from app.models.entities import (
    AdminAlert,
    Delivery,
    DeliveryPartner,
    Order,
    Restaurant,
    User,
)
from app.websockets.manager import manager

router = APIRouter(tags=["Live Tracking"])


async def authenticate_websocket(websocket: WebSocket):
    token = websocket.query_params.get("token")

    if not token:
        await websocket.close(code=1008, reason="Authentication required")
        return None

    payload = decode_access_token(token)

    if not payload or not payload.get("sub"):
        await websocket.close(code=1008, reason="Invalid or expired token")
        return None

    try:
        user_id = int(payload["sub"])
    except (ValueError, TypeError):
        await websocket.close(code=1008, reason="Invalid token")
        return None

    async with AsyncSessionLocal() as db:
        user = await db.get(User, user_id)

        if user is None or not user.is_active:
            await websocket.close(code=1008, reason="Inactive or unknown user")
            return None

        return {"id": user.id, "role": user.role}


async def load_order_tracking(order_id: int):
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Order)
            .options(
                selectinload(Order.restaurant),
                selectinload(Order.delivery)
                .selectinload(Delivery.delivery_partner)
                .selectinload(DeliveryPartner.user),
            )
            .where(Order.id == order_id)
        )
        order = result.scalar_one_or_none()

        if order is None:
            return None

        delivery = order.delivery
        partner = delivery.delivery_partner if delivery else None

        latitude = None
        longitude = None
        partner_name = None
        vehicle_type = None

        if partner:
            latitude = (
                delivery.current_latitude
                if delivery.current_latitude is not None
                else partner.current_latitude
            )
            longitude = (
                delivery.current_longitude
                if delivery.current_longitude is not None
                else partner.current_longitude
            )
            partner_name = partner.user.full_name if partner.user else None
            vehicle_type = partner.vehicle_type

        return {
            "type": "tracking_update",
            "order_id": order.id,
            "order_number": order.order_number,
            "status": order.status,
            "restaurant": order.restaurant.name if order.restaurant else None,
            "partner": {
                "name": partner_name,
                "vehicle_type": vehicle_type,
                "latitude": latitude,
                "longitude": longitude,
            },
            "distance_remaining_km": None,
            "eta_minutes": None,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }


async def can_track_order(user_info: dict, order_id: int) -> bool:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Order)
            .options(
                selectinload(Order.restaurant),
                selectinload(Order.delivery),
            )
            .where(Order.id == order_id)
        )
        order = result.scalar_one_or_none()

        if order is None:
            return False

        if user_info["role"] == "Admin":
            return True

        if order.customer_id == user_info["id"]:
            return True

        restaurant = order.restaurant
        if (
            user_info["role"] == "RestaurantOwner"
            and restaurant is not None
            and restaurant.owner_id == user_info["id"]
        ):
            return True

        if user_info["role"] == "DeliveryPartner" and order.delivery:
            partner_result = await db.execute(
                select(DeliveryPartner.id).where(
                    DeliveryPartner.user_id == user_info["id"],
                    DeliveryPartner.id
                    == order.delivery.delivery_partner_id,
                )
            )
            return partner_result.scalar_one_or_none() is not None

        return False


@router.websocket("/ws/orders/{order_id}")
async def order_tracking(websocket: WebSocket, order_id: int):
    user_info = await authenticate_websocket(websocket)
    if user_info is None:
        return

    if not await can_track_order(user_info, order_id):
        await websocket.close(
            code=1008,
            reason="Not allowed to track this order",
        )
        return

    await manager.connect_order(order_id, websocket)

    try:
        snapshot = await load_order_tracking(order_id)
        if snapshot is None:
            await websocket.close(code=1008, reason="Order not found")
            return

        await websocket.send_json(snapshot)

        while True:
            message = await websocket.receive_json()

            if message.get("type") == "location_update":
                if user_info["role"] != "DeliveryPartner":
                    await websocket.send_json({
                        "type": "error",
                        "detail": (
                            "Only delivery partners can send location updates"
                        ),
                    })
                    continue

                try:
                    latitude = float(message["latitude"])
                    longitude = float(message["longitude"])
                    if not (
                        -90 <= latitude <= 90
                        and -180 <= longitude <= 180
                    ):
                        raise ValueError
                except (KeyError, TypeError, ValueError):
                    await websocket.send_json({
                        "type": "error",
                        "detail": (
                            "Valid latitude and longitude are required"
                        ),
                    })
                    continue

                async with AsyncSessionLocal() as db:
                    result = await db.execute(
                        select(Order)
                        .options(selectinload(Order.delivery))
                        .where(Order.id == order_id)
                    )
                    order = result.scalar_one_or_none()

                    if order is None or order.delivery is None:
                        await websocket.send_json({
                            "type": "error",
                            "detail": "This order has no delivery record",
                        })
                        continue

                    partner = await db.scalar(
                        select(DeliveryPartner).where(
                            DeliveryPartner.user_id == user_info["id"]
                        )
                    )

                    if (
                        partner is None
                        or order.delivery.delivery_partner_id != partner.id
                    ):
                        await websocket.send_json({
                            "type": "error",
                            "detail": "You are not assigned to this order",
                        })
                        continue

                    order.delivery.current_latitude = latitude
                    order.delivery.current_longitude = longitude
                    partner.current_latitude = latitude
                    partner.current_longitude = longitude
                    await db.commit()

                snapshot = await load_order_tracking(order_id)
                if snapshot:
                    await manager.broadcast_order(order_id, snapshot)

    except WebSocketDisconnect:
        pass
    except Exception:
        await websocket.close(code=1011, reason="WebSocket error")
    finally:
        await manager.disconnect_order(order_id, websocket)


@router.websocket("/ws/restaurant")
async def restaurant_alerts(websocket: WebSocket):
    user_info = await authenticate_websocket(websocket)
    if user_info is None:
        return

    if user_info["role"] not in {"RestaurantOwner", "Admin"}:
        await websocket.close(
            code=1008,
            reason="Restaurant owner access required",
        )
        return

    await manager.connect_restaurant(websocket)
    try:
        await websocket.send_json({
            "type": "connected",
            "message": "Restaurant order alerts connected",
        })
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect_restaurant(websocket)


@router.websocket("/ws/partner")
async def partner_offers(websocket: WebSocket):
    user_info = await authenticate_websocket(websocket)
    if user_info is None:
        return

    if user_info["role"] not in {"DeliveryPartner", "Admin"}:
        await websocket.close(
            code=1008,
            reason="Delivery partner access required",
        )
        return

    await manager.connect_partner(websocket)
    try:
        await websocket.send_json({
            "type": "connected",
            "message": "Delivery partner offer connection established",
        })
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect_partner(websocket)


@router.websocket("/ws/admin")
async def admin_alerts(websocket: WebSocket):
    user_info = await authenticate_websocket(websocket)
    if user_info is None:
        return

    if user_info["role"] != "Admin":
        await websocket.close(
            code=1008,
            reason="Admin access required",
        )
        return

    await manager.connect_admin(websocket)

    try:
        await websocket.send_json({
            "type": "connected",
            "message": "Admin alert connection established",
        })

        # Replay persisted alerts so an Admin can catch up after reconnecting.
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(AdminAlert).order_by(
                    AdminAlert.created_at.desc(),
                    AdminAlert.id.desc(),
                ).limit(100)
            )
            alerts = result.scalars().all()

            for alert in alerts:
                await websocket.send_json({
                    "type": "admin_alert",
                    "alert_id": alert.id,
                    "alert_key": alert.alert_key,
                    "order_id": alert.order_id,
                    "alert_type": alert.alert_type,
                    "message": alert.message,
                    "created_at": (
                        alert.created_at.isoformat()
                        if alert.created_at
                        else None
                    ),
                })

        while True:
            await websocket.receive_text()

    except WebSocketDisconnect:
        pass
    except Exception:
        try:
            await websocket.close(
                code=1011,
                reason="Admin WebSocket error",
            )
        except Exception:
            pass
    finally:
        await manager.disconnect_admin(websocket)

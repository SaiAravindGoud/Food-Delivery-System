
import asyncio

from sqlalchemy import select

from app.database import AsyncSessionLocal, engine
from app.models.entities import Coupon, Order


async def main():
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(
                    Order.id,
                    Order.status,
                    Order.coupon_id,
                ).order_by(Order.id)
            )

            print("\nORDERS")
            for row in result.all():
                print(row)

            result = await db.execute(
                select(
                    Coupon.id,
                    Coupon.code,
                    Coupon.used_count,
                ).order_by(Coupon.id)
            )

            print("\nCOUPONS")
            for row in result.all():
                print(row)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

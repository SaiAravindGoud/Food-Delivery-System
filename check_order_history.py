
import asyncio

from sqlalchemy import select

from app.database import AsyncSessionLocal, engine
from app.models.entities import OrderStatusHistory


async def main():
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(
                    OrderStatusHistory.id,
                    OrderStatusHistory.order_id,
                    OrderStatusHistory.old_status,
                    OrderStatusHistory.new_status,
                    OrderStatusHistory.changed_by_user_id,
                    OrderStatusHistory.notes,
                    OrderStatusHistory.created_at,
                )
                .where(OrderStatusHistory.order_id == 2)
                .order_by(OrderStatusHistory.created_at)
            )

            print("\nORDER 2 STATUS HISTORY")
            for row in result.all():
                print(row)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

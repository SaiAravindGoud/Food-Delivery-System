
import asyncio

from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models.entities import Delivery


async def main():
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Delivery).where(Delivery.id == 1)
        )
        delivery = result.scalar_one_or_none()

        if delivery is None:
            print("Delivery ID 1 not found.")
            return

        print("Delivery ID:", delivery.id)
        print("Delivery status:", delivery.status)
        print("Delivery verified:", delivery.delivery_verified)
        print("Current delivery OTP:", delivery.delivery_otp)


if __name__ == "__main__":
    asyncio.run(main())

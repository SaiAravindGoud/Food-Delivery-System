
import asyncio
import getpass

from sqlalchemy import select

from app.auth.security import hash_password
from app.database import AsyncSessionLocal
from app.models.entities import User


async def main():
    print("Create Delivery Partner Account")
    full_name = input("Full name: ").strip()
    email = input("Email: ").strip().lower()
    password = getpass.getpass("Password (8-72 characters): ")

    if len(full_name) < 2:
        print("Error: Full name must contain at least 2 characters.")
        return

    if len(password) < 8 or len(password) > 72:
        print("Error: Password must be between 8 and 72 characters.")
        return

    async with AsyncSessionLocal() as db:
        existing = await db.scalar(
            select(User).where(User.email == email)
        )

        if existing:
            print(
                f"Error: An account already exists for {email}. "
                f"Existing role: {existing.role}"
            )
            return

        user = User(
            full_name=full_name,
            email=email,
            hashed_password=hash_password(password),
            role="DeliveryPartner",
            is_active=True,
            is_verified=True,
        )

        db.add(user)

        try:
            await db.commit()
            await db.refresh(user)
        except Exception:
            await db.rollback()
            raise

        print("\nDelivery partner account created successfully.")
        print(f"User ID: {user.id}")
        print(f"Name: {user.full_name}")
        print(f"Email: {user.email}")
        print(f"Role: {user.role}")
        print("Next: Create the delivery partner profile in Swagger.")


if __name__ == "__main__":
    asyncio.run(main())

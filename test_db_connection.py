
import asyncio

from sqlalchemy import text

from app.database import engine


async def main():
    try:
        async with engine.connect() as connection:
            result = await connection.execute(text("SELECT 1"))
            print("Database connection successful!")
            print("Test query result:", result.scalar())
    except Exception as error:
        print("Database connection failed:")
        print(error)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())

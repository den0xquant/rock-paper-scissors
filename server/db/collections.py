import motor.motor_asyncio
from server.config import settings


async_client = motor.motor_asyncio.AsyncIOMotorClient(settings.MONGODB_URI)
async_db = async_client.get_database(settings.MONGODB_DB)

users_collection = async_db.get_collection("users")
rooms_collection = async_db.get_collection("rooms")

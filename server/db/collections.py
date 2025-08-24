import motor.motor_asyncio
from server.config import settings


client = motor.motor_asyncio.AsyncIOMotorClient(settings.MONGODB_URI)
db = client.get_database(settings.MONGODB_DB)

users_collection = db.get_collection("users")
rooms_collection = db.get_collection("rooms")
wins_collection = db.get_collection("wins")
losses_collection = db.get_collection("losses")

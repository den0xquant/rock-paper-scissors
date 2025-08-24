from fastapi import APIRouter, Request
from aiogram.types import Update


webhook_router = APIRouter(tags=["webhook"])


@webhook_router.post("/webhook")
async def handle_webhook(request: Request) -> dict:
    payload = await request.json()
    # Process the webhook payload
    print(Update.model_validate(payload))
    print(payload)
    return {"status": "success"}

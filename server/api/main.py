from fastapi import APIRouter
from server.api.routes import (
    web, ws
)


api_router = APIRouter(tags=["api"])
api_router.include_router(web.web_router)
api_router.include_router(ws.ws_router)

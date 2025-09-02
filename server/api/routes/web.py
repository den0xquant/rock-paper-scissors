import jwt
from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, FileResponse
from pydantic import BaseModel

from server.config import settings


class SessionToken(BaseModel):
    t: str


class JwtSession(BaseModel):
    tg_id: int
    user_id: int
    username: str
    jwt: str
    ctx: dict[str, str]


web_router = APIRouter(tags=["web"])


@web_router.get("/", response_class=HTMLResponse)
def index():
    return FileResponse("client/index.html", media_type="text/html; charset=utf-8")


@web_router.post("/session")
def jwt_session(t: SessionToken) -> JwtSession:
    try:
        payload = jwt.decode(t.t, settings.SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    return JwtSession(
        tg_id=payload.get("tg_id"),
        user_id=payload.get("user_id"),
        username=payload.get("username"),
        jwt=t.t,
        ctx=payload.get("ctx", {})
    )

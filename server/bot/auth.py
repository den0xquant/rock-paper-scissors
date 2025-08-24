import jwt
import time

from aiogram.types import CallbackQuery

from server.config import settings


def session_token(cb: CallbackQuery):
    """Creates JWT token for user session.

    Args:
        cb (CallbackQuery): Aiogram object CallbackQuery.

    Returns:
        str: JWT token.
    """
    payload = {
        'tg_id': cb.from_user.id,
        'un': cb.from_user.username or 'Guest',
        'inline_message_id': cb.inline_message_id,
        'chat_id': cb.message.chat.id if cb.message else None,
        'message_id': cb.message.message_id if cb.message else None,
        'iat': int(time.time()),
        'exp': int(time.time()) + 86400,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm='HS256')

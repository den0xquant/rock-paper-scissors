from aiogram import Router, F
from aiogram.types import Message, InlineQuery, InlineQueryResultGame, CallbackQuery
from aiogram.filters import CommandStart

from server.config import settings
from server.bot.keyboards import create_inline_keyboard
from server.bot.auth import session_token


router = Router(name="common")


@router.message(CommandStart())
async def cmd_start(message: Message) -> None:
    await message.answer_game(game_short_name=settings.GAME_SHORT_NAME, reply_markup=create_inline_keyboard())


@router.inline_query()
async def inline_query_handler(inline_query: InlineQuery) -> None:
    await inline_query.answer(results=[
        InlineQueryResultGame(id="1", game_short_name=settings.GAME_SHORT_NAME)
    ])


@router.callback_query(F.game_short_name == settings.GAME_SHORT_NAME)
async def callback_game_handler(callback_query: CallbackQuery) -> None:
    t = session_token(callback_query)
    url = f"http://192.168.100.40:8000/?t={t}&tg_id={callback_query.from_user.id}"
    await callback_query.answer(url=url)

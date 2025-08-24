from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup, CallbackGame


def create_inline_keyboard():
    markup = InlineKeyboardMarkup(
        inline_keyboard=[[
            InlineKeyboardButton(
                text='▶ Play',
                callback_game=CallbackGame()
            )
        ]]
    )
    return markup

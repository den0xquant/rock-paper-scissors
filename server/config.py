import secrets
import string
import warnings
from typing import Annotated, Any, Literal

from pydantic import (
    AnyUrl,
    BeforeValidator,
    computed_field,
    model_validator,
)
from pydantic_core import MultiHostUrl
from pydantic_settings import BaseSettings, SettingsConfigDict
from typing_extensions import Self


def parse_cors(v: Any) -> list[str] | str:
    if isinstance(v, str) and not v.startswith("["):
        return [i.strip() for i in v.split(",")]
    elif isinstance(v, list | str):
        return v
    else:
        raise ValueError("CORS origins must be a string or a list of strings.")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_ignore_empty=True,
        extra="ignore",
    )
    BOT_TOKEN: str
    GAME_SHORT_NAME: str = "rps_game"
    API_V1_STR: str = ""
    SECRET_KEY: str = secrets.token_urlsafe(32)
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60 * 24 * 7  # 7 days
    ENVIRONMENT: Literal["local", "staging", "production"] = "local"

    BACKEND_CORS_ORIGINS: Annotated[
        list[AnyUrl] | str, BeforeValidator(parse_cors)
    ] = []

    @computed_field
    @property
    def all_cors_origins(self) -> list[str]:
        return [str(origin).rstrip("/") for origin in self.BACKEND_CORS_ORIGINS]

    PROJECT_NAME: str
    SENTRY_DSN: str | None = None

    MONGODB_USER: str
    MONGODB_PASSWORD: str
    MONGODB_SERVER: str = "localhost"
    MONGODB_PORT: int = 27017
    MONGODB_DB: str = "rock_paper_scissors"

    REDIS_HOST: str = "localhost"
    REDIS_PORT: int = 6379
    REDIS_DB: int = 0
    REDIS_CACHE_PREFIX: str = "rooms:"

    symbols: str = string.ascii_letters + string.digits
    BASE: int = len(symbols)
    SHORT_URL_LENGTH: int = 7

    @computed_field
    @property
    def REDIS_URI(self) -> str:
        return f"redis://{self.REDIS_HOST}:{self.REDIS_PORT}/{self.REDIS_DB}?decode_responses=True"

    @computed_field
    @property
    def MONGODB_URI(self) -> str:
        return f"mongodb://{self.MONGODB_USER}:{self.MONGODB_PASSWORD}@{self.MONGODB_SERVER}:{self.MONGODB_PORT}/{self.MONGODB_DB}?retryWrites=true&w=majority&authSource=admin"

    def _check_default_secret(self, var_name: str, value: str | None) -> None:
        if value == "changethis":
            message = (
                f"The value of {var_name} is 'changethis', "
                f"for security, please change it in your .env file."
            )
            if self.ENVIRONMENT == "local":
                warnings.warn(message, stacklevel=1)
            else:
                raise ValueError(message)

    @model_validator(mode="after")
    def _enforce_non_default_secrets(self) -> Self:
        self._check_default_secret("SECRET_KEY", self.SECRET_KEY)
        self._check_default_secret("MONGODB_USER", self.MONGODB_USER)
        self._check_default_secret("MONGODB_SERVER", self.MONGODB_SERVER)
        self._check_default_secret("MONGODB_DB", self.MONGODB_DB)
        self._check_default_secret("MONGODB_PASSWORD", self.MONGODB_PASSWORD)
        return self


settings = Settings()  # type: ignore

from typing import Annotated

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_ignore_empty=True)

    EDSTEM_API_KEY: str
    EDSTEM_EMAIL: str
    EDSTEM_PASSWORD: str

    COURSE_ID: int

    SLACK_APP_TOKEN: str
    SLACK_BOT_TOKEN: str
    SLACK_CHANNEL_ID: str
    SLACK_CANARY_CHANNEL_ID: str

    DISCORD_WEBHOOK_URL: str | None = None

    FACULTY_UNIQNAMES: Annotated[list[str], NoDecode] = []

    AUTO_HELP_AFTER_MINUTES: float = 60
    AUTO_HELP_MAX_AGE_HOURS: float = 24
    AUTO_HELP_INTERVAL_SECONDS: float = 60
    POST_DELAY_MINUTES: float = 0
    SKIP_STAFF_POSTS: bool = True

    @field_validator("FACULTY_UNIQNAMES", mode="before")
    @classmethod
    def split_uniqnames(cls, val: object) -> object:
        if isinstance(val, str):
            return [uniqname.strip() for uniqname in val.split(",") if uniqname.strip()]

        return val


# required fields are filled from .env but the type checker doesn't know that
settings = Settings()  # type: ignore[call-arg]

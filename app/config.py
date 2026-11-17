from dataclasses import dataclass
import os

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    database_url: str
    openai_api_key: str = ""
    openai_model: str = ""
    actor: str = "local-demo-reviewer"
    openai_reasoning_effort: str = "high"

    @classmethod
    def from_env(cls):
        load_dotenv(override=False)
        return cls(
            database_url=os.getenv("DATABASE_URL", "sqlite:///./.local/telecom.db"),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("OPENAI_MODEL", ""),
            openai_reasoning_effort=os.getenv("OPENAI_REASONING_EFFORT", "high"),
        )

    @property
    def openai_configured(self):
        return bool(self.openai_api_key and self.openai_model)

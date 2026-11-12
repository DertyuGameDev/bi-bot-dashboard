from pathlib import Path
from typing import Literal
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RPA_", env_file=ROOT / ".env", extra="ignore")
    source: Literal["postgres", "demo"] = "postgres"
    database_url: str = ""
    model_adapter: Literal["codex", "demo"] = "codex"
    model: str = "gpt-6.1-terra"
    reasoning: str = "medium"
    model_timeout: int = 120
    agent_max_steps: int = 24
    agent_timeout: int = 900
    chat_history: int = 100
    dashboard_brief_enabled: bool = True
    preflight_mode: Literal["off", "local", "model", "agent", "decision"] = "agent"
    decision_url: str = "http://127.0.0.1:8009"
    decision_timeout: float = Field(3.0, gt=0, le=30)
    query_timeout_ms: int = 15000
    row_limit: int = 500000
    storage: str = str(ROOT / ".local/state.sqlite3")
    host: str = "127.0.0.1"
    port: int = 8080
    secure_cookie: bool = False
    public_base_url: str = ""
    public_address_file: str = str(ROOT / ".local/public-address.json")

    @field_validator("decision_url")
    @classmethod
    def local_decision_url(cls, value):
        from shared.decision_preflight import loopback_url

        return loopback_url(value)

    @field_validator("reasoning")
    @classmethod
    def reasoning_alias(cls, value):
        value = "medium" if value == "mid" else value
        if value not in {"none", "low", "medium", "high", "xhigh", "max"}:
            raise ValueError("Неизвестный reasoning effort")
        return value

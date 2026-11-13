from pathlib import Path
from typing import Literal
import json
from urllib.parse import urlsplit
from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic_settings.sources import DotEnvSettingsSource

ROOT = Path(__file__).resolve().parents[1]


class PrivateModelEnv(DotEnvSettingsSource):
    def _read_env_file(self, file_path):
        from dotenv import dotenv_values

        values = dict(super()._read_env_file(file_path))
        raw = dotenv_values(file_path, encoding=self.env_file_encoding, interpolate=False)
        for name, value in raw.items():
            if name.lower() in {"rpa_model_api_key", "rpa_model_api_headers"}:
                values[name if self.case_sensitive else name.lower()] = value
        return values


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="RPA_", env_file=ROOT / ".env", extra="ignore", hide_input_in_errors=True
    )

    @classmethod
    def settings_customise_sources(
        cls, settings_cls, init_settings, env_settings, dotenv_settings, file_secret_settings
    ):
        return (
            init_settings,
            env_settings,
            PrivateModelEnv(
                settings_cls,
                env_file=dotenv_settings.env_file,
                env_file_encoding=dotenv_settings.env_file_encoding,
            ),
            file_secret_settings,
        )

    source: Literal["postgres", "demo"] = "postgres"
    database_url: str = ""
    model_adapter: Literal[
        "codex",
        "openai",
        "openai_compatible",
        "anthropic",
        "gemini",
        "openrouter",
        "deepseek",
        "ollama",
        "lmstudio",
        "demo",
    ] = "codex"
    model: str = "gpt-6.1-terra"
    model_base_url: str = ""
    model_api_key: SecretStr = SecretStr("")
    model_api_headers: SecretStr = SecretStr("{}")
    model_api_format: Literal["auto", "json_schema", "json_object", "prompt"] = "auto"
    model_api_reasoning: Literal["", "none", "minimal", "low", "medium", "high", "xhigh", "max"] = ""
    model_max_output_tokens: int = Field(8192, ge=256, le=128000)
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

    @field_validator("model_base_url")
    @classmethod
    def api_url(cls, value):
        value = value.strip().rstrip("/")
        if value:
            u = urlsplit(value)
            if (
                u.scheme not in {"http", "https"}
                or not u.hostname
                or u.username
                or u.password
                or u.query
                or u.fragment
            ):
                raise ValueError("Base URL должен быть HTTP(S)-адресом без пароля, query и fragment")
            if any(c.isspace() for c in value) or u.port == 0:
                raise ValueError("Некорректный адрес API")
        return value

    @field_validator("model_api_key")
    @classmethod
    def api_key_text(cls, value):
        text = value.get_secret_value()
        if any(ord(c) < 32 or ord(c) > 126 for c in text):
            raise ValueError("API-ключ должен содержать только печатные ASCII-символы")
        return value

    @field_validator("model_api_headers")
    @classmethod
    def api_headers(cls, value):
        if not value.get_secret_value():
            return SecretStr("{}")
        try:
            headers = json.loads(value.get_secret_value())
            if not isinstance(headers, dict) or any(
                not isinstance(k, str)
                or not isinstance(v, str)
                or not k
                or any(ord(c) < 32 or ord(c) > 126 for c in k + v)
                or k.lower() in {"host", "content-length"}
                for k, v in headers.items()
            ):
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("Дополнительные API-заголовки должны быть JSON-объектом строк") from None
        return value

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

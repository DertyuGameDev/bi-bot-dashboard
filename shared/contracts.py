"""Common strict boundary for tool and model payloads."""

from pydantic import BaseModel, ConfigDict


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

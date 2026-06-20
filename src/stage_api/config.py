"""Configuration model and loader.

This container controls exactly **one** CONEX-AGP stage.  Its configuration is a
single flat YAML file mounted into the container and read once at startup.  A
``device`` of ``emulator://`` is backed by an in-process
:class:`~stage_api.emulator.EmulatedController` instead of real hardware -- the
API and client code are identical either way.  To control several stages, run
several instances of this container.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

EMULATOR_SCHEME = "emulator://"


class Config(BaseModel):
    # -- the stage ----------------------------------------------------------
    device: str = Field(..., description="Serial device path, or 'emulator://'.")
    address: int = Field(1, ge=1, le=31, description="RS-485 controller address.")
    label: str | None = Field(
        None, description="Human-readable label for this stage (metadata only)."
    )
    auto_home: bool = Field(
        False, description="Home the stage on startup (causes physical motion)."
    )
    travel: tuple[float, float] | None = Field(
        None, description="[min, max] software limits in stage units; queried if omitted."
    )
    units: str | None = Field(None, description="Display units, metadata only (e.g. 'mm').")

    # -- emulator-only settings (ignored for real hardware) -----------------
    speed: float = Field(5.0, gt=0, description="Emulated travel speed (units/s).")
    home_position: float = Field(0.0, description="Emulated HOME position.")
    home_time: float = Field(1.5, ge=0, description="Emulated homing duration (s).")

    # -- service tuning -----------------------------------------------------
    poll_interval: float = Field(0.25, gt=0, description="Status poll period for ?wait (s).")
    default_timeout: float = Field(30.0, gt=0, description="Default ?wait timeout (s).")

    @property
    def is_emulated(self) -> bool:
        return self.device.strip().lower().startswith(EMULATOR_SCHEME)

    @field_validator("travel")
    @classmethod
    def _check_travel(cls, v):
        if v is not None and v[0] > v[1]:
            raise ValueError("travel must be [min, max] with min <= max")
        return v

    @model_validator(mode="after")
    def _default_label(self) -> "Config":
        if not self.label:
            # Fall back to the device's basename ('/dev/newport-focus' -> 'newport-focus'),
            # or a generic name for the emulator.
            base = self.device.strip().rstrip("/").rsplit("/", 1)[-1]
            self.label = base if base and not self.is_emulated else "stage"
        return self


def load_config(path: str | Path) -> Config:
    path = Path(path)
    data = yaml.safe_load(path.read_text()) or {}
    return Config.model_validate(data)

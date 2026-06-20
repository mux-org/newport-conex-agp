"""Request/response schemas for the REST API."""

from __future__ import annotations

from pydantic import BaseModel, Field


class MoveAbsRequest(BaseModel):
    position: float = Field(..., description="Absolute target position in stage units.")


class MoveRelRequest(BaseModel):
    displacement: float = Field(..., description="Relative displacement in stage units.")


class StateResponse(BaseModel):
    state: str
    position: float | None = None
    target: float | None = None
    referenced: bool | None = None
    fault_flags: str | None = None
    connected: bool


class StageSummary(BaseModel):
    label: str
    device: str
    address: int
    emulated: bool
    connected: bool
    units: str | None = None


class LimitsResponse(BaseModel):
    low: float
    high: float
    units: str | None = None


class InfoResponse(BaseModel):
    version: str
    model: str


class HealthResponse(BaseModel):
    status: str
    connected: bool
    state: str


class CommandAccepted(BaseModel):
    state: str
    target: float | None = None
    position: float | None = None
    waited: bool = False

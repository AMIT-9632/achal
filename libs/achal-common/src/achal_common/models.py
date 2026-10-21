# SPDX-License-Identifier: MIT
"""Pydantic mirrors of the JSON schemas in /schemas. Keep the two in sync."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class Level(StrEnum):
    NORMAL = "normal"
    WATCH = "watch"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return ["normal", "watch", "warning", "critical"].index(self.value)


class Truth(BaseModel):
    """Simulation-only ground truth. event: none|blast|trough|precursor|collapse|fire."""

    subsidence_mm: float | None = None
    event: str | None = None
    fault: str | None = None
    hazard: bool | None = None
    onset_ts: datetime | None = None
    failure_ts: datetime | None = None


class Telemetry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = 1
    site: str
    node: str = Field(pattern=r"^n[0-9]{2}$")
    seq: int = Field(ge=0)
    ts: datetime
    rx_ts: datetime | None = None
    hops: int | None = Field(default=None, ge=0)
    path: list[str] | None = None
    rssi_dbm: float | None = None
    snr_db: float | None = None
    neighbour_rssi: dict[str, float] | None = None
    tilt_x_deg: float
    tilt_y_deg: float
    vib_rms_g: float = Field(ge=0)
    vib_peak_g: float | None = Field(default=None, ge=0)
    crack_mm: float
    ext_mm: float
    temp_c: float | None = None
    batt_v: float
    buffered: bool | None = None
    truth: Truth | None = None


class RiskAssessment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = 1
    site: str
    node: str
    ts: datetime
    score: float = Field(ge=0, le=1)
    level: Level
    ttf_hours: float | None = None
    components: dict[str, float] | None = None
    reasons: list[str] | None = None
    agreeing_neighbours: int | None = Field(default=None, ge=0)


class LatLon(BaseModel):
    lat: float
    lon: float


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = 1
    id: str
    site: str
    ts: datetime
    level: Level
    previous_level: Level | None = None
    nodes: list[str]
    centroid: LatLon | None = None
    ttf_hours: float | None = None
    reasons: list[str]
    siren: bool | None = None


class RiskGrid(BaseModel):
    """Risk raster on achal/{site}/risk/grid (schemas/risk-grid.v1.json)."""

    model_config = ConfigDict(extra="forbid")

    v: Literal[1] = 1
    site: str
    ts: datetime
    x0_m: float
    y0_m: float
    dx_m: float = Field(gt=0)
    dy_m: float = Field(gt=0)
    rows: int = Field(ge=1)
    cols: int = Field(ge=1)
    values: list[float]
    method: Literal["idw", "kriging"]

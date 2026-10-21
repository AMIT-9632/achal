# SPDX-License-Identifier: MIT
"""Site configuration loader. One YAML per coalfield in config/sites/."""

from __future__ import annotations

import math
import os
from pathlib import Path

import yaml
from pydantic import BaseModel

EARTH_RADIUS_M = 6_371_000.0


class Point(BaseModel):
    x: float
    y: float


class Panel(BaseModel):
    width_m: float
    length_m: float
    seam_depth_m: float
    extraction_height_m: float
    method: str
    angle_of_draw_deg: float


class Grid(BaseModel):
    rows: int
    cols: int


class Mesh(BaseModel):
    grid: Grid
    spacing_m: float
    radio: str
    sample_period_s: int
    max_hops: int


class Gateway(BaseModel):
    id: str
    position_m: Point


class Siren(BaseModel):
    id: str
    position_m: Point
    label: str


class Exposure(BaseModel):
    households: int
    people: int
    roads_m: float = 0
    rail_m: float = 0


class Origin(BaseModel):
    lat: float
    lon: float


class SiteConfig(BaseModel):
    site_id: str
    name: str
    district: str
    state: str
    languages: list[str]
    origin: Origin
    panel: Panel
    mesh: Mesh
    gateway: Gateway
    sirens: list[Siren]
    exposure: Exposure
    alert_levels: list[str]

    def node_ids(self) -> list[str]:
        return [f"n{i:02d}" for i in range(self.mesh.grid.rows * self.mesh.grid.cols)]

    def node_position_m(self, node_id: str) -> Point:
        """Local metres from the panel origin; nodes fill the grid row by row."""
        i = int(node_id[1:])
        row, col = divmod(i, self.mesh.grid.cols)
        offset_x = (self.panel.width_m - (self.mesh.grid.cols - 1) * self.mesh.spacing_m) / 2
        offset_y = (self.panel.length_m - (self.mesh.grid.rows - 1) * self.mesh.spacing_m) / 2
        return Point(x=offset_x + col * self.mesh.spacing_m, y=offset_y + row * self.mesh.spacing_m)

    def to_latlon(self, p: Point) -> tuple[float, float]:
        """Equirectangular approximation, fine at panel scale (hundreds of metres)."""
        lat = self.origin.lat + math.degrees(p.y / EARTH_RADIUS_M)
        lon = self.origin.lon + math.degrees(p.x / (EARTH_RADIUS_M * math.cos(math.radians(self.origin.lat))))
        return lat, lon


def config_dir() -> Path:
    return Path(os.environ.get("ACHAL_CONFIG_DIR", Path(__file__).resolve().parents[4] / "config"))


def load_site(site_id: str | None = None) -> SiteConfig:
    site_id = site_id or os.environ.get("SITE_ID", "jharia-demo")
    path = config_dir() / "sites" / f"{site_id}.yaml"
    with path.open(encoding="utf-8") as f:
        return SiteConfig.model_validate(yaml.safe_load(f))

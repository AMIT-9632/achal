# SPDX-License-Identifier: MIT
"""Simulated LoRa surface mesh (IN865) between the field nodes and the gateway.

The model is deliberately simple enough to read in one sitting, but every number
in it is physical and documented in docs/mesh.md:

* PHY: SX1276 LoRa, 125 kHz, CR 4/5, time on air from the Semtech AN1200.13
  formula, receiver sensitivity and demodulation SNR floor per spreading factor.
* Channel: log-distance path loss with a static per-link shadowing term
  (log-normal, symmetric) and per-packet fast fading, plus thermal noise.
* MAC: unconfirmed ALOHA on the three IN865 default channels, with the
  collision chance from the offered load; link-layer ACK and up to
  `max_retries` retransmissions per hop. A lost ACK produces a duplicate.
* Routing: gateway-rooted, minimum expected-transmission-count (ETX) tree,
  limited to `max_hops`, rebuilt from beacons every `route_refresh_s`.
  Between rebuilds a node that gets no ACK from its parent blacklists it and
  repairs locally by switching to its best alternate parent.
* Failures: a fraction of nodes can die at a given time, without warning.

`Mesh.transmit(reading)` returns the copies of the reading that reach the
gateway: none (lost, node dead, no route), one, or two (duplicate).
Everything is deterministic for a given seed.
"""

from __future__ import annotations

import heapq
import math
import os
import random
import struct
from dataclasses import dataclass, field
from datetime import datetime

from achal_common import Telemetry
from achal_common.config import SiteConfig

GATEWAY = "gw"

# SX1276 datasheet (rev. 7), table 10, BW = 125 kHz.
SENSITIVITY_DBM = {7: -123.0, 8: -126.0, 9: -129.0, 10: -132.0, 11: -134.5, 12: -137.0}
# Semtech AN1200.22, demodulator SNR floor per spreading factor.
SNR_MIN_DB = {7: -7.5, 8: -10.0, 9: -12.5, 10: -15.0, 11: -17.5, 12: -20.0}
# LoRaWAN Regional Parameters RP002, IN865 default channels (MHz).
IN865_CHANNELS_MHZ = (865.0625, 865.4025, 865.9850)

# On-air frame of a node reading (see docs/mesh.md for the field list):
# src, seq, hop count, flags | tilt x, tilt y (1e-4 deg) | vib rms, peak (1e-5 g)
# | crack, ext (1e-2 mm) | temp (1e-1 C) | batt (mV) | relay id x4 | CRC16
FRAME = struct.Struct("<BHBBiiHHhhhH4BH")


def encoded_frame(reading: Telemetry) -> bytes:
    """Compact binary frame a real node would send. Used for the payload size."""
    def clip(v: float, lo: int, hi: int) -> int:
        return max(lo, min(hi, round(v)))

    body = FRAME.pack(
        int(reading.node[1:]), reading.seq & 0xFFFF, 0, 0,
        clip(reading.tilt_x_deg * 1e4, -2**31, 2**31 - 1), clip(reading.tilt_y_deg * 1e4, -2**31, 2**31 - 1),
        clip(reading.vib_rms_g * 1e5, 0, 0xFFFF), clip((reading.vib_peak_g or 0) * 1e5, 0, 0xFFFF),
        clip(reading.crack_mm * 100, -32768, 32767), clip(reading.ext_mm * 100, -32768, 32767),
        clip((reading.temp_c or 0) * 10, -32768, 32767), clip(reading.batt_v * 1000, 0, 0xFFFF),
        0, 0, 0, 0, 0,
    )
    return body


PAYLOAD_BYTES = FRAME.size
ACK_BYTES = 4  # src, dst, seq


def airtime_s(payload_bytes: int, sf: int, bw_hz: float = 125_000, cr: int = 1, preamble: int = 8,
              explicit_header: bool = True, crc: bool = True) -> float:
    """LoRa time on air, Semtech AN1200.13 section 4. cr=1 means coding rate 4/5."""
    t_sym = (2 ** sf) / bw_hz
    de = 1 if t_sym > 0.016 else 0  # low data rate optimisation, mandatory above 16 ms symbols
    ih = 0 if explicit_header else 1
    num = 8 * payload_bytes - 4 * sf + 28 + 16 * int(crc) - 20 * ih
    n_payload = 8 + max(math.ceil(num / (4 * (sf - 2 * de))) * (cr + 4), 0)
    return (preamble + 4.25) * t_sym + n_payload * t_sym


@dataclass
class RadioProfile:
    """Radio and channel assumptions. Defaults are documented in docs/mesh.md."""

    sf: int = 7
    bw_hz: float = 125_000
    tx_power_dbm: float = 14.0          # well under the IN865 limit, saves battery
    node_antenna_gain_dbi: float = 2.15  # quarter-wave whip
    gateway_antenna_gain_dbi: float = 5.0  # collinear on a 6 m mast
    cable_loss_db: float = 0.5
    noise_figure_db: float = 6.0
    path_loss_exponent: float = 4.0     # near-ground antennas, spoil heaps, houses
    ref_distance_m: float = 1.0
    shadowing_sigma_db: float = 7.0     # static, per link
    fading_sigma_db: float = 3.0        # per packet
    max_retries: int = 3
    route_refresh_s: float = 300.0      # beacon and routing-table period
    dead_after_beacons: int = 3          # neighbour dropped after this many missed beacons
    link_margin_db: float = 3.0          # links weaker than sensitivity + margin are not used for routing
    listen_before_talk: bool = True      # channel activity detection before each mesh frame
    cad_window_s: float = 0.002          # ~2 symbols at SF7: two senders this close in time still collide

    @property
    def freq_mhz(self) -> float:
        return IN865_CHANNELS_MHZ[1]

    @property
    def noise_floor_dbm(self) -> float:
        return -174.0 + 10 * math.log10(self.bw_hz) + self.noise_figure_db

    def path_loss_db(self, d_m: float) -> float:
        d = max(d_m, self.ref_distance_m)
        wavelength = 299.792458 / self.freq_mhz
        pl0 = 20 * math.log10(4 * math.pi * self.ref_distance_m / wavelength)
        return pl0 + 10 * self.path_loss_exponent * math.log10(d / self.ref_distance_m)


PROFILES = {
    # Defaults, see docs/mesh.md.
    "baseline": RadioProfile(),
    # Sensitivity check: denser clutter and deeper shadowing than we expect.
    "harsh": RadioProfile(path_loss_exponent=4.4, shadowing_sigma_db=9.0, fading_sigma_db=4.0),
}


@dataclass
class MeshStats:
    generated: int = 0          # readings produced by live nodes
    delivered: int = 0          # unique readings that reached the gateway
    duplicates: int = 0         # extra copies that reached the gateway
    lost_link: int = 0          # dropped after all retries
    lost_no_route: int = 0      # node cut off from the gateway
    dead_node_readings: int = 0  # readings a dead node would have sent (not counted in PDR)
    tx_attempts: int = 0
    collisions: int = 0
    local_repairs: int = 0
    hops_total: int = 0
    airtime_s: float = 0.0       # total time on air, all nodes, data + ACKs
    per_node: dict[str, list[int]] = field(default_factory=dict)  # node -> [generated, delivered]
    latency_s: list[float] = field(default_factory=list)

    @property
    def pdr(self) -> float:
        return self.delivered / self.generated if self.generated else 1.0

    def worst_node_pdr(self) -> float | None:
        ratios = [d / g for g, d in self.per_node.values() if g]
        return min(ratios) if ratios else None

    def summary(self) -> dict:
        lat = sorted(self.latency_s)
        p95 = lat[int(0.95 * (len(lat) - 1))] if lat else None
        return {
            "generated": self.generated, "delivered": self.delivered, "pdr": round(self.pdr, 5),
            "duplicates": self.duplicates, "lost_link": self.lost_link, "lost_no_route": self.lost_no_route,
            "tx_attempts": self.tx_attempts, "collisions": self.collisions, "local_repairs": self.local_repairs,
            "mean_hops": round(self.hops_total / self.delivered, 3) if self.delivered else None,
            "latency_p95_s": round(p95, 3) if p95 is not None else None,
            "tx_per_delivered": round(self.tx_attempts / self.delivered, 3) if self.delivered else None,
            "worst_node_pdr": round(w, 5) if (w := self.worst_node_pdr()) is not None else None,
            "airtime_s": round(self.airtime_s, 3),
        }


class Mesh:
    """Multi-hop LoRa mesh for one site. `mode="star"` gives the single-hop baseline."""

    def __init__(self, site: SiteConfig, radio: RadioProfile | None = None, seed: int = 42,
                 fail_fraction: float = 0.0, fail_at_s: float = 0.0, mode: str = "mesh",
                 max_hops: int | None = None, fail_pattern: str = "random") -> None:
        if mode not in ("mesh", "star"):
            raise ValueError(f"unknown mesh mode {mode!r}")
        self.site = site
        self.radio = radio or RadioProfile()
        self.mode = mode
        self.max_hops = 1 if mode == "star" else (max_hops or site.mesh.max_hops)
        self.rng = random.Random(seed)
        self.nodes = site.node_ids()
        self.pos = {n: site.node_position_m(n) for n in self.nodes}
        self.pos[GATEWAY] = site.gateway.position_m
        self.period_s = site.mesh.sample_period_s
        self.stats = MeshStats()

        # Static shadowing per unordered link, drawn once so the topology is stable.
        self.shadow: dict[frozenset[str], float] = {}
        everyone = [*self.nodes, GATEWAY]
        for i, a in enumerate(everyone):
            for b in everyone[i + 1:]:
                self.shadow[frozenset((a, b))] = self.rng.gauss(0, self.radio.shadowing_sigma_db)

        self._rssi_cache: dict[tuple[str, str], float] = {}
        # Mesh: one SF everywhere (short hops). Star baseline: each node picks the lowest SF that
        # reaches the gateway with margin, like LoRaWAN ADR, falling back to SF12.
        self.sf = {n: self.radio.sf for n in everyone}
        if mode == "star":
            for n in self.nodes:
                ok = [sf for sf in range(self.radio.sf, 13)
                      if self.mean_rssi_dbm(n, GATEWAY) >= self.rx_floor_dbm(sf) + self.radio.link_margin_db]
                self.sf[n] = ok[0] if ok else 12

        n_fail = round(fail_fraction * len(self.nodes))
        self.fail_at_s = fail_at_s
        if fail_pattern == "random":
            self.failing = set(self.rng.sample(self.nodes, n_fail))
        elif fail_pattern == "cluster":
            # Correlated loss, e.g. a pothole or fire takes out a patch of nodes together.
            c = self.pos[self.rng.choice(self.nodes)]
            by_distance = sorted(self.nodes, key=lambda n: math.dist((self.pos[n].x, self.pos[n].y), (c.x, c.y)))
            self.failing = set(by_distance[:n_fail])
        else:
            raise ValueError(f"unknown fail pattern {fail_pattern!r}")

        self.t0: datetime | None = None
        self.now_s = 0.0
        self.next_refresh_s = 0.0
        self.parent: dict[str, str | None] = {}
        self.rank: dict[str, float] = {}
        self.hops_to_gw: dict[str, int] = {}
        self.blacklist: dict[str, set[str]] = {n: set() for n in self.nodes}
        self.seen_at_relay: dict[str, set[tuple[str, int]]] = {n: set() for n in self.nodes}
        self._load_per_s = self._occupancy = 0.0
        self.with_neighbour_rssi = True
        # Share of node pairs that cannot hear each other (hidden terminals for listen-before-talk).
        pairs = [(a, b) for i, a in enumerate(self.nodes) for b in self.nodes[i + 1:]]
        hidden = sum(self.mean_rssi_dbm(a, b) < SENSITIVITY_DBM[self.radio.sf] for a, b in pairs)
        self._hidden_fraction = hidden / len(pairs) if pairs else 0.0
        self._rebuild_routes()

    # ---- channel -------------------------------------------------------

    def mean_rssi_dbm(self, tx: str, rx: str) -> float:
        """Received power without fast fading. Links are reciprocal, so this is cached per pair."""
        key = (tx, rx) if tx < rx else (rx, tx)
        cached = self._rssi_cache.get(key)
        if cached is None:
            r = self.radio
            far_gain = r.gateway_antenna_gain_dbi if GATEWAY in key else r.node_antenna_gain_dbi
            d = math.dist((self.pos[tx].x, self.pos[tx].y), (self.pos[rx].x, self.pos[rx].y))
            cached = (r.tx_power_dbm + r.node_antenna_gain_dbi + far_gain - 2 * r.cable_loss_db
                      - r.path_loss_db(d) - self.shadow[frozenset(key)])
            self._rssi_cache[key] = cached
        return cached

    def rx_floor_dbm(self, sf: int) -> float:
        return max(SENSITIVITY_DBM[sf], self.radio.noise_floor_dbm + SNR_MIN_DB[sf])

    def link_prr(self, tx: str, rx: str, sf: int | None = None) -> float:
        """Expected packet reception ratio of one attempt, ignoring collisions."""
        sf = sf or self.sf[tx]
        z = (self.mean_rssi_dbm(tx, rx) - self.rx_floor_dbm(sf)) / self.radio.fading_sigma_db
        return 0.5 * math.erfc(-z / math.sqrt(2))

    def _attempt(self, tx: str, rx: str, sf: int, payload: int) -> tuple[bool, float, float]:
        """One transmission. Returns (received, rssi, snr)."""
        self.stats.tx_attempts += 1
        t_air = airtime_s(payload, sf, self.radio.bw_hz)
        self.stats.airtime_s += t_air
        rssi = self.mean_rssi_dbm(tx, rx) + self.rng.gauss(0, self.radio.fading_sigma_db)
        snr = rssi - self.radio.noise_floor_dbm
        ok = rssi >= SENSITIVITY_DBM[sf] and snr >= SNR_MIN_DB[sf]
        # Pure ALOHA: we collide if another frame on our channel starts within [t - T_other, t + T_own].
        # Expected number of such frames = rate * T_own + channel occupancy. No capture effect (pessimistic).
        # With listen-before-talk only hidden senders (ones we cannot hear) and near-simultaneous starts
        # still collide. The star baseline is LoRaWAN class A, which does not listen first.
        if self.mode == "mesh" and self.radio.listen_before_talk:
            h = self._hidden_fraction
            exposure = (h * (self._load_per_s * t_air + self._occupancy)
                        + (1 - h) * self._load_per_s * self.radio.cad_window_s)
        else:
            exposure = self._load_per_s * t_air + self._occupancy
        p_clear = math.exp(-exposure)
        if ok and self.rng.random() > p_clear:
            self.stats.collisions += 1
            ok = False
        return ok, rssi, snr

    # ---- routing -------------------------------------------------------

    def alive(self, n: str) -> bool:
        return n == GATEWAY or n not in self.failing or self.now_s < self.fail_at_s

    def _believed_alive(self, n: str) -> bool:
        """Dead nodes stay in routing tables until they miss enough beacons."""
        if self.alive(n):
            return True
        detect_s = self.fail_at_s + self.radio.dead_after_beacons * self.radio.route_refresh_s
        return self.now_s < detect_s

    def usable(self, a: str, b: str) -> bool:
        return self.mean_rssi_dbm(a, b) >= self.rx_floor_dbm(self.sf[a]) + self.radio.link_margin_db

    def _rebuild_routes(self) -> None:
        """Dijkstra from the gateway on ETX = 1/PRR, bounded by max_hops."""
        candidates = [n for n in self.nodes if self._believed_alive(n)]
        self.rank = {GATEWAY: 0.0}
        self.hops_to_gw = {GATEWAY: 0}
        self.parent = {n: None for n in self.nodes}
        heap: list[tuple[float, int, str]] = [(0.0, 0, GATEWAY)]
        done: set[str] = set()
        while heap:
            cost, hops, u = heapq.heappop(heap)
            if u in done:
                continue
            done.add(u)
            if hops >= self.max_hops:
                continue
            for v in candidates:
                if v in done or not self.usable(v, u):
                    continue
                etx = 1.0 / max(self.link_prr(v, u) * self.link_prr(u, v, self.sf[v]), 1e-6)
                if cost + etx < self.rank.get(v, math.inf):
                    self.rank[v] = cost + etx
                    self.hops_to_gw[v] = hops + 1
                    self.parent[v] = u
                    heapq.heappush(heap, (cost + etx, hops + 1, v))
        for n in self.nodes:
            self.blacklist[n].clear()
            self.seen_at_relay[n].clear()
        # Offered load per channel: every live node's frame crosses hops_to_gw links, each with an ACK.
        frames = occupancy = 0.0
        for n in self.nodes:
            if self.alive(n) and n in self.hops_to_gw:
                sf = self.sf[n]
                t = airtime_s(PAYLOAD_BYTES, sf, self.radio.bw_hz) + airtime_s(ACK_BYTES, sf, self.radio.bw_hz)
                frames += 2 * self.hops_to_gw[n]
                occupancy += t * self.hops_to_gw[n]
        per_channel = self.period_s * len(IN865_CHANNELS_MHZ)
        self._load_per_s = frames / per_channel
        self._occupancy = occupancy / per_channel
        self.next_refresh_s = self.now_s + self.radio.route_refresh_s

    def _next_hop(self, node: str) -> str | None:
        p = self.parent.get(node)
        if p is not None and p not in self.blacklist[node]:
            return p
        # Local repair: best neighbour that is strictly closer to the gateway, so no loops.
        my_rank = self.rank.get(node, math.inf)
        best, best_cost = None, math.inf
        for nb, nb_rank in self.rank.items():
            if nb in (node, p) or nb in self.blacklist[node] or nb_rank >= my_rank:
                continue
            if not self.usable(node, nb) or self.hops_to_gw[nb] + 1 > self.max_hops:
                continue
            cost = nb_rank + 1.0 / max(self.link_prr(node, nb) * self.link_prr(nb, node, self.sf[node]), 1e-6)
            if cost < best_cost:
                best, best_cost = nb, cost
        return best

    # ---- public --------------------------------------------------------

    def neighbour_rssi(self, node: str, limit: int = 8) -> dict[str, float]:
        """RSSI this node hears from live neighbours' beacons (strongest first)."""
        heard = {}
        for nb in self.nodes:
            if nb == node or not self.alive(nb):
                continue
            rssi = self.mean_rssi_dbm(nb, node) + self.rng.gauss(0, self.radio.fading_sigma_db / 2)
            if rssi >= SENSITIVITY_DBM[self.radio.sf]:
                heard[nb] = round(rssi, 1)
        return dict(sorted(heard.items(), key=lambda kv: -kv[1])[:limit])

    def advance(self, t_s: float) -> None:
        self.now_s = t_s
        if t_s >= self.next_refresh_s:
            self._rebuild_routes()

    def transmit(self, reading: Telemetry) -> list[Telemetry]:
        """Route one reading to the gateway. Returns the copies the gateway receives."""
        if self.t0 is None:
            self.t0 = reading.ts
        self.advance((reading.ts - self.t0).total_seconds())
        src = reading.node
        if not self.alive(src):
            self.stats.dead_node_readings += 1
            return []
        self.stats.generated += 1
        counts = self.stats.per_node.setdefault(src, [0, 0])
        counts[0] += 1

        payload = PAYLOAD_BYTES
        path, node, latency = [src], src, 0.0
        copies = 0
        last_rssi = last_snr = None
        key = (src, reading.seq)

        while node != GATEWAY:
            nxt = self._next_hop(node)
            if nxt is None or len(path) > self.max_hops:
                self.stats.lost_no_route += 1
                return []
            received = False
            sf = self.sf[node]
            t_data = airtime_s(payload, sf, self.radio.bw_hz)
            t_ack = airtime_s(ACK_BYTES, sf, self.radio.bw_hz)
            for _attempt in range(self.radio.max_retries + 1):
                latency += t_data + t_ack + self.rng.uniform(0, 4 * t_data)  # random backoff
                ok = False
                if self.alive(nxt):
                    ok, rssi, snr = self._attempt(node, nxt, sf, payload)
                if not ok:
                    continue
                if nxt == GATEWAY:
                    copies += 1
                    last_rssi, last_snr = rssi, snr
                received = True
                ack_ok, _, _ = self._attempt(nxt, node, sf, ACK_BYTES)
                if ack_ok:
                    break
                # ACK lost: the sender retries and the receiver gets the frame again.
            if not received:
                # Parent looks dead or out of reach: blacklist it and try an alternate parent.
                # With no alternate (always so in the star baseline) the node keeps its parent.
                self.blacklist[node].add(nxt)
                if self._next_hop(node) is None:
                    self.blacklist[node].discard(nxt)
                    self.stats.lost_link += 1
                    return []
                self.stats.local_repairs += 1
                continue
            if nxt != GATEWAY:
                if key in self.seen_at_relay[nxt]:
                    self.stats.lost_link += 1  # routing loop; the relay drops what it already forwarded
                    return []
                self.seen_at_relay[nxt].add(key)
                path.append(nxt)
            node = nxt

        self.stats.delivered += 1
        counts[1] += 1
        self.stats.duplicates += copies - 1
        self.stats.hops_total += len(path)
        self.stats.latency_s.append(latency)
        out = reading.model_copy(update={
            "hops": len(path), "path": path, "rssi_dbm": round(last_rssi, 1), "snr_db": round(last_snr, 1),
            "neighbour_rssi": self.neighbour_rssi(src) if self.with_neighbour_rssi else None,
        })
        return [out] * copies


def mesh_from_env(site: SiteConfig) -> Mesh:
    """Build the mesh for field-sim from MESH_* environment variables (see .env.example)."""
    env = os.environ.get
    return Mesh(
        site, PROFILES[env("MESH_PROFILE", "baseline")],
        seed=int(env("MESH_SEED", env("SIM_SEED", "42"))),
        fail_fraction=float(env("MESH_FAIL_FRACTION", "0")),
        fail_at_s=float(env("MESH_FAIL_AT_S", "3600")),
        fail_pattern=env("MESH_FAIL_PATTERN", "random"),
        mode=env("MESH_MODE", "mesh"),
    )

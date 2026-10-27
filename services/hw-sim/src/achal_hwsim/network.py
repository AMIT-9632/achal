# SPDX-License-Identifier: MIT
"""Stage 3: the three network architectures of the simulation guide, section 4.

    A  wifi_mesh   ESP32 nodes form an ESP-WIFI-MESH tree; the root bridges to the
                   Raspberry Pi (Wi-Fi AP). Multi-hop.
    B  lorawan     every node talks LoRaWAN class A straight to a Pi with an
                   8-channel concentrator. Single hop, pure ALOHA.
    C  hybrid      option A on site, plus a separate long-range LoRa link from the
                   Pi to the mine office (2-10 km) that carries summaries and events.

Each link is modelled on its own, as the guide asks; Wi-Fi mesh and LoRaWAN are
never assumed to interoperate.

Channel (both radios): dual-slope two-ray path loss (free space up to the
breakpoint 4 h_t h_r / lambda, 40 dB/decade beyond), a static log-normal
shadowing term per link (symmetric, sigma_sh) and per-packet Gaussian fading
(sigma_f). Packet error is a logistic curve in received power, centred so that
PER = 10 % at the data-sheet sensitivity (the 802.11 and LoRa definitions of
sensitivity), slope 1.5 per dB (ASSUMPTION, a common link-abstraction choice).

Wi-Fi MAC: 802.11 DCF with up to 7 retries, binary exponential backoff
(CWmin 15, slot 9 us), DIFS 34 us, SIFS 16 us, MCS0 6.5 Mb/s. Contention between
nodes is ignored: at 20 nodes x 1 packet/s the channel is < 1 % busy (reported).
The ns-3 802.11s cross-check (ns3/) covers what this abstraction leaves out.
Mesh forwarding: per-hop software delay U(2, 8) ms (ASSUMPTION for the
ESP-WIFI-MESH stack); a child whose parent dies is cut off for
`reparent_s` (ASSUMPTION 5 s: beacon loss detection + re-association) and
keeps up to `node_queue` packets meanwhile. The root keeps `root_queue`
packets while the Pi is unreachable and flushes them when it returns.

LoRaWAN: unconfirmed uplinks, SF chosen by ADR with a 10 dB margin, random
channel among `channels`, collision if two packets of the same SF overlap on a
channel unless one is >= 6 dB stronger (capture). Inter-SF interference is
ignored. A gateway outage loses everything sent meanwhile.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np

from .components.esp32 import lora_airtime_s

C_LIGHT = 299_792_458.0


@dataclass
class Radio:
    freq_hz: float
    tx_dbm: float
    sens_dbm: float
    h_tx_m: float = 1.0
    h_rx_m: float = 1.0
    sigma_sh_db: float = 4.0
    sigma_f_db: float = 2.5
    clutter_db: float = 0.0  # extra fixed loss: enclosure wall, stake, detuning near the ground
    excess_db_per_m: float = 0.05  # ASSUMPTION: scattered obstructions (huts, spoil heaps, scrub)

    @property
    def wavelength(self) -> float:
        return C_LIGHT / self.freq_hz

    def path_loss_db(self, d_m: float, h_tx: float | None = None, h_rx: float | None = None) -> float:
        d = max(d_m, 1.0)
        ht, hr = h_tx or self.h_tx_m, h_rx or self.h_rx_m
        lam = self.wavelength
        fspl = lambda x: 20 * math.log10(4 * math.pi * x / lam)  # noqa: E731
        d_b = 4 * ht * hr / lam
        pl = fspl(d) if d <= d_b else fspl(d_b) + 40 * math.log10(d / d_b)
        return pl + self.clutter_db + self.excess_db_per_m * d


def per(rx_dbm: float, sens_dbm: float, slope: float = 1.5) -> float:
    """Packet error rate: 10 % at the sensitivity, logistic in dB."""
    centre = sens_dbm - math.log(9) / slope
    return 1.0 / (1.0 + math.exp(slope * (rx_dbm - centre)))


WIFI = Radio(2.437e9, tx_dbm=17.0, sens_dbm=-93.0, clutter_db=6.0)  # ESP32 11n MCS0 sensitivity
LORA_SENS = {7: -123.0, 8: -126.0, 9: -129.0, 10: -132.0, 11: -134.5, 12: -137.0}  # SX1276, 125 kHz


def lora_radio(sf: int = 7) -> Radio:
    return Radio(865.4e6, tx_dbm=17.0, sens_dbm=LORA_SENS[sf], clutter_db=6.0)


@dataclass
class Failures:
    node_down: dict[int, list[tuple[float, float]]] = field(default_factory=dict)
    gw_down: list[tuple[float, float]] = field(default_factory=list)
    extra_loss_db: list[tuple[float, float, float]] = field(default_factory=list)  # (t0, t1, dB): rain, dust, foliage

    def node_up(self, i: int, t: float) -> bool:
        return not any(a <= t < b for a, b in self.node_down.get(i, []))

    def gw_up(self, t: float) -> bool:
        return not any(a <= t < b for a, b in self.gw_down)

    def extra(self, t: float) -> float:
        return sum(db for a, b, db in self.extra_loss_db if a <= t < b)

    def gw_back(self, t: float) -> float:
        for a, b in self.gw_down:
            if a <= t < b:
                return b
        return t


@dataclass
class Delivery:
    idx: int
    seq: int
    t_tx: float
    t_rx: float | None  # None = lost
    hops: int
    rssi_dbm: float | None
    path: tuple[int, ...] = ()
    buffered: bool = False
    cause: str = ""  # lost: link | collision | no_route | gw_down | queue_full | node_down


class Topology:
    def __init__(self, xy: np.ndarray, gw_xy: tuple[float, float], radio: Radio, seed: int, gw_h_m: float = 3.0):
        self.xy, self.gw_xy, self.radio = xy, np.array(gw_xy, float), radio
        self.n = len(xy)
        rng = np.random.default_rng([seed, 7])
        pts = np.vstack([xy, self.gw_xy])
        m = len(pts)
        self.dist = np.hypot(*(pts[:, None, :] - pts[None, :, :]).transpose(2, 0, 1))
        sh = rng.normal(0, radio.sigma_sh_db, (m, m))
        self.shadow = np.triu(sh, 1) + np.triu(sh, 1).T
        self.mean_rx = np.full((m, m), -999.0)
        for a in range(m):
            for b in range(m):
                if a != b:
                    ht = gw_h_m if a == m - 1 else radio.h_tx_m
                    hr = gw_h_m if b == m - 1 else radio.h_rx_m
                    self.mean_rx[a, b] = radio.tx_dbm - radio.path_loss_db(self.dist[a, b], ht, hr) - self.shadow[a, b]
        self.GW = m - 1


class WifiMesh:
    """Option A. Tree routing like ESP-WIFI-MESH: fewest layers, then strongest parent."""

    def __init__(
        self,
        topo: Topology,
        rng: np.random.Generator,
        failures: Failures | None = None,
        parent_min_dbm: float = -82.0,
        max_layer: int = 6,
        reparent_s: float = 5.0,
        node_queue: int = 32,
        root_queue: int = 4000,
        payload_bytes: int = 64,
        pi_parse_s: tuple[float, float] = (0.001, 0.003),
        hop_sw_s: tuple[float, float] = (0.002, 0.008),
    ):
        self.pi_parse_s, self.hop_sw_s = pi_parse_s, hop_sw_s
        self.t, self.rng, self.f = topo, rng, failures or Failures()
        self.parent_min, self.max_layer, self.reparent_s = parent_min_dbm, max_layer, reparent_s
        self.node_queue, self.root_queue, self.payload = node_queue, root_queue, payload_bytes
        self._trees: dict[frozenset, dict[int, int]] = {}

    def tree(self, dead: frozenset) -> dict[int, int]:
        """parent of every reachable node (GW = the Pi's AP) given the set of dead nodes."""
        if dead in self._trees:
            return self._trees[dead]
        T = self.t
        parent: dict[int, int] = {}
        layer = {T.GW: 0}
        frontier = [T.GW]
        for lyr in range(1, self.max_layer + 1):
            nxt = []
            for i in range(T.n):
                if i in layer or i in dead:
                    continue
                cands = [(T.mean_rx[p, i], p) for p in frontier if T.mean_rx[p, i] >= self.parent_min]
                if cands:
                    _, p = max(cands)
                    parent[i], layer[i] = p, lyr
                    nxt.append(i)
            if not nxt:
                break
            frontier = nxt
        self._trees[dead] = parent
        return parent

    def _hop(self, a: int, b: int, t: float) -> tuple[bool, float, float]:
        """One 802.11 hop with retries. Returns (ok, delay_s, rx_dbm of the last attempt)."""
        T, R = self.t, self.t.radio
        tx_s = 20e-6 + (self.payload + 36) * 8 / 6.5e6
        ack_s = 16e-6 + 20e-6 + 14 * 8 / 6.5e6
        cw, delay = 15, 0.0
        rx = T.mean_rx[a, b] - self.f.extra(t)
        for _ in range(8):  # first try + 7 retries
            delay += 34e-6 + self.rng.integers(0, cw + 1) * 9e-6 + tx_s + ack_s
            rx_k = rx + self.rng.normal(0, R.sigma_f_db)
            if self.rng.random() > per(rx_k, R.sens_dbm):
                return True, delay, rx_k
            cw = min(2 * cw + 1, 1023)
        return False, delay, rx

    def channel_busy_fraction(self, n_pkts_per_s: float, mean_hops: float) -> float:
        tx_s = 34e-6 + 7.5 * 9e-6 + 20e-6 + (self.payload + 36) * 8 / 6.5e6 + 16e-6 + 37e-6
        return n_pkts_per_s * mean_hops * tx_s

    def deliver(self, idx: int, seq: int, t: float) -> Delivery:
        T, f = self.t, self.f
        if not f.node_up(idx, t):
            return Delivery(idx, seq, t, None, 0, None, cause="node_down")
        dead = frozenset(i for i in range(T.n) if not f.node_up(i, t))
        parent = self.tree(dead)
        if idx not in parent:
            return Delivery(idx, seq, t, None, 0, None, cause="no_route")
        # Was this node's route broken in the last reparent_s by a parent death? Then it waits, queued.
        now, at, path, hops, buffered, rx = t, idx, [idx], 0, False, None
        recent_dead = frozenset(i for i in range(T.n) if not f.node_up(i, t - self.reparent_s))
        if recent_dead != dead:
            old = self.tree(recent_dead)
            k, broken = idx, False
            while k in old:
                if not f.node_up(old[k], t):
                    broken = True
                    break
                k = old[k]
            if broken:
                # Held in the node's queue until it has re-associated with a new parent.
                died = max(
                    b
                    for i in range(T.n)
                    for a, b in [(0, 0)] + [(a, a) for a, _ in f.node_down.get(i, [])]
                    if t - self.reparent_s <= b <= t
                )
                now = max(now, died + self.reparent_s)
                buffered = True
        while at != T.GW:
            nxt = parent[at]
            if nxt == T.GW and not f.gw_up(now):
                # Root holds the packet until the Pi is back (bounded queue, drop-oldest not modelled per packet).
                back = f.gw_back(now)
                if (back - now) * T.n > self.root_queue:
                    return Delivery(idx, seq, t, None, hops, None, tuple(path), cause="queue_full")
                now, buffered = back, True
            ok, d, rx = self._hop(at, nxt, now)
            now += d
            hops += 1
            if not ok:
                return Delivery(idx, seq, t, None, hops, None, tuple(path), cause="link")
            if nxt != T.GW:
                now += self.rng.uniform(*self.hop_sw_s)
                path.append(nxt)
            at = nxt
        now += self.rng.uniform(*self.pi_parse_s)  # Pi socket + parse
        return Delivery(idx, seq, t, now, hops, rx, tuple(path), buffered)


class LoRaWanStar:
    """Option B: all nodes single-hop to the concentrator; pure ALOHA with capture."""

    def __init__(
        self,
        topo: Topology,
        rng: np.random.Generator,
        failures: Failures | None = None,
        channels: int = 3,
        payload_bytes: int = 32,
        adr_margin_db: float = 10.0,
        capture_db: float | None = 6.0,
    ):
        self.t, self.rng, self.f = topo, rng, failures or Failures()
        self.capture_db = capture_db
        self.channels, self.payload = channels, payload_bytes + 13  # LoRaWAN MHDR+FHDR+FPort+MIC
        self.sf = {}
        for i in range(topo.n):
            rx = topo.mean_rx[i, topo.GW]
            self.sf[i] = next((sf for sf in range(7, 13) if rx - LORA_SENS[sf] >= adr_margin_db), 12)
        self.toa = {sf: lora_airtime_s(self.payload, sf) for sf in range(7, 13)}

    def deliver_batch(self, sends: list[tuple[int, int, float]]) -> list[Delivery]:
        """sends: (idx, seq, t). Collisions need everything at once."""
        T, f, R = self.t, self.f, self.t.radio
        tx = []
        for idx, seq, t in sends:
            sf = self.sf[idx]
            ch = int(self.rng.integers(self.channels))
            rx = T.mean_rx[idx, T.GW] - f.extra(t) + self.rng.normal(0, R.sigma_f_db)
            tx.append((t, t + self.toa[sf], idx, seq, sf, ch, rx))
        tx.sort()
        out = []
        for k, (t0, t1, idx, seq, sf, ch, rx) in enumerate(tx):
            if not f.node_up(idx, t0):
                out.append(Delivery(idx, seq, t0, None, 1, None, cause="node_down"))
                continue
            overl = [
                o
                for o in tx[max(0, k - 200) : k + 200]
                if o is not tx[k] and o[5] == ch and o[4] == sf and o[0] < t1 and o[1] > t0 and f.node_up(o[2], o[0])
            ]
            if overl and (self.capture_db is None or rx < max(o[6] for o in overl) + self.capture_db):
                out.append(Delivery(idx, seq, t0, None, 1, None, cause="collision"))
                continue
            if self.rng.random() < per(rx, LORA_SENS[sf]):
                out.append(Delivery(idx, seq, t0, None, 1, None, cause="link"))
                continue
            if not f.gw_up(t1):
                out.append(Delivery(idx, seq, t0, None, 1, None, cause="gw_down"))
                continue
            out.append(Delivery(idx, seq, t0, t1 + self.rng.uniform(0.005, 0.02), 1, rx))
        return out

    def offered_load(self, interval_s: float) -> float:
        """ALOHA G per channel."""
        return sum(self.toa[self.sf[i]] for i in range(self.t.n)) / interval_s / self.channels


@dataclass
class Backhaul:
    """Option C uplink: Pi -> mine office over a LoRa point-to-point link (Okumura-Hata, open area)."""

    distance_km: float = 3.0
    h_pi_m: float = 10.0
    h_office_m: float = 10.0
    sf: int | None = None
    tx_dbm: float = 20.0
    ant_gain_dbi: float = 5.0  # each end, small collinear
    terrain_loss_db: float = 20.0  # ASSUMPTION: spoil heaps, buildings; Hata is optimistic below 30 m masts
    summary_every_s: float = 10.0
    bytes_per_node: int = 6
    retries: int = 3

    def path_loss_db(self) -> float:
        f, hb, hm, d = 865.0, self.h_pi_m, self.h_office_m, max(self.distance_km, 0.1)
        a_hm = (1.1 * math.log10(f) - 0.7) * hm - (1.56 * math.log10(f) - 0.8)
        urban = (
            69.55
            + 26.16 * math.log10(f)
            - 13.82 * math.log10(hb)
            - a_hm
            + (44.9 - 6.55 * math.log10(hb)) * math.log10(d)
        )
        return urban - 4.78 * math.log10(f) ** 2 + 18.33 * math.log10(f) - 40.94  # open-area correction

    def rx_dbm(self) -> float:
        return self.tx_dbm + 2 * self.ant_gain_dbi - self.path_loss_db() - self.terrain_loss_db

    def pick_sf(self, margin_db: float = 10.0) -> int:
        rx = self.rx_dbm()
        return self.sf or next((sf for sf in range(7, 13) if rx - LORA_SENS[sf] >= margin_db), 12)

    def frame_bytes(self, n_nodes: int) -> int:
        return 8 + self.bytes_per_node * n_nodes

    def utilisation_raw(self, n_nodes: int, interval_s: float, payload: int = 32) -> float:
        """If every reading were forwarded as is."""
        return n_nodes / interval_s * lora_airtime_s(payload + 8, self.pick_sf())

    def utilisation_summary(self, n_nodes: int) -> float:
        return (
            lora_airtime_s(min(self.frame_bytes(n_nodes), 222), self.pick_sf())
            * math.ceil(self.frame_bytes(n_nodes) / 222)
            / self.summary_every_s
        )

    def event_latency_s(self, rng: np.random.Generator) -> tuple[float | None, int]:
        """An alert frame (16 B) sent at once, preempting the next summary; retried on loss."""
        sf = self.pick_sf()
        toa = lora_airtime_s(16, sf)
        p_ok = 1 - per(self.rx_dbm() + rng.normal(0, 2.5), LORA_SENS[sf])
        wait = rng.uniform(0, lora_airtime_s(min(self.frame_bytes(20), 222), sf))  # a summary may be on air
        t = wait
        for k in range(self.retries + 1):
            t += toa + 0.05
            if rng.random() < p_ok:
                return t, k
            t += lora_airtime_s(4, sf) + 1.0 + rng.uniform(0, 1.0)  # ack timeout + backoff
        return None, self.retries


def simulate(
    arch: str,
    xy: np.ndarray,
    interval_s: float,
    duration_s: float,
    seed: int,
    failures: Failures | None = None,
    gw_xy: tuple[float, float] | None = None,
    wifi_kw: dict | None = None,
    lora_kw: dict | None = None,
    radio: Radio | None = None,
    sends: list[tuple[int, int, float]] | None = None,
) -> list[Delivery]:
    """Run one architecture. `sends` overrides the default periodic traffic (used by the end-to-end run)."""
    rng = np.random.default_rng([seed, 99])
    if gw_xy is None:
        gw_xy = (float(xy[:, 0].min() - 10.0), float(xy[:, 1].mean()))
    if sends is None:
        offs = rng.uniform(0, interval_s, len(xy))
        sends = [
            (i, k, k * interval_s + offs[i] + rng.normal(0, 0.002))
            for k in range(int(duration_s / interval_s))
            for i in range(len(xy))
        ]
    if arch in ("wifi_mesh", "hybrid"):
        # Option C's on-site part is option A; its backhaul is modelled by Backhaul (summaries + events).
        topo = Topology(xy, gw_xy, radio or WIFI, seed)
        net = WifiMesh(topo, rng, failures, **(wifi_kw or {}))
        return [net.deliver(i, k, t) for i, k, t in sends]
    if arch == "lorawan":
        topo = Topology(xy, gw_xy, lora_radio(7), seed)
        net = LoRaWanStar(topo, rng, failures, **(lora_kw or {}))
        return net.deliver_batch(sends)
    raise ValueError(arch)


def metrics(dl: list[Delivery], n_nodes: int, interval_s: float, duration_s: float) -> dict:
    sent = len(dl)
    ok = [d for d in dl if d.t_rx is not None]
    delays = np.array([d.t_rx - d.t_tx for d in ok]) if ok else np.array([np.nan])
    per_node = np.zeros(n_nodes)
    for d in ok:
        per_node[d.idx] += 1
    expected = duration_s / interval_s
    causes: dict[str, int] = {}
    for d in dl:
        if d.t_rx is None:
            causes[d.cause] = causes.get(d.cause, 0) + 1
    return {
        "sent": sent,
        "delivered": len(ok),
        "pdr": len(ok) / sent if sent else float("nan"),
        "delay_ms_p50": float(np.nanpercentile(delays, 50) * 1000),
        "delay_ms_p95": float(np.nanpercentile(delays, 95) * 1000),
        "delay_ms_p99": float(np.nanpercentile(delays, 99) * 1000),
        "delay_ms_max": float(np.nanmax(delays) * 1000),
        "mean_hops": float(np.mean([d.hops for d in ok])) if ok else float("nan"),
        "max_hops": int(max((d.hops for d in ok), default=0)),
        "worst_node_pdr": float(per_node.min() / expected),
        "node_availability": float((per_node > 0).mean()),
        "losses": causes,
    }

# SPDX-License-Identifier: MIT
import numpy as np
from achal_hwsim import scenarios
from achal_hwsim.components import esp32, mpu6050, power, vl53l0x


def test_vl53l0x_range_limits_follow_the_datasheet():
    rng = np.random.default_rng(1)
    shade = vl53l0x.TofConditions(plate_half_mm=1000.0)
    default = vl53l0x.Vl53l0x(rng, mode="default").max_range_mm(shade)
    long = vl53l0x.Vl53l0x(rng, mode="long_range").max_range_mm(shade)
    assert 1000 < default < 1400  # datasheet: 1.2 m on white, default mode
    assert 1700 < long < 2200  # datasheet: 2 m in long-range mode
    # The guide's caveat: no use as a node-to-node gauge at mesh spacing.
    s = vl53l0x.Vl53l0x(rng).burst(10_000, shade, 7)
    assert all(x[1] != 0 or x[0] >= 8190 for x in s)


def test_mpu6050_noise_floor_is_datasheet_density():
    floor = mpu6050.tilt_noise_floor_deg(200)
    assert 0.005 < floor < 0.05


def test_cell_and_charger_behave():
    c = power.Cell()
    assert 3.0 < power.ocv(0.05) < power.ocv(0.5) < power.ocv(1.0) <= 4.2
    hot = power.Tp4056(ntc_wired=True).charge_current(c, power.Panel(), 1000.0, 50.0, 55.0)
    assert hot[0] == 0.0  # NTC wired: no charging above 45-50 degC


def test_lora_airtime_matches_semtech_calculator():
    # SX1276 calculator: 32 B, SF7, 125 kHz, CR 4/5, 8 symbol preamble -> 71.94 ms
    assert abs(esp32.lora_airtime_s(32, 7) - 0.07194) < 0.002


def test_scenarios_are_labelled():
    sc = scenarios.build("critical", seed=1, n=6, duration_s=300)
    labels = {sc.label(i, t) for i in range(sc.n) for t in (0.0, 299.0)}
    assert 0 in labels and 2 in labels
    normal = scenarios.build("normal", seed=1, n=6, duration_s=300)
    assert max(normal.label(i, 299.0) for i in range(normal.n)) == 0

import numpy as np
import pytest

from xrd_engine.electrochem.cex_dqdv import DqdvSettings, _continuous_runs, _curve
from xrd_engine.electrochem.cex_native import CexError


@pytest.mark.parametrize("polarity", [1, -1])
def test_known_peak_and_signed_capacity_survive_smoothing(polarity):
    voltage = np.linspace(2.8, 4.2, 1401)[::polarity]
    profile = 3 + 40 * np.exp(-0.5 * ((voltage - 3.65) / 0.03) ** 2)
    capacity = np.r_[0.0, np.cumsum(0.0005 * (profile[1:] + profile[:-1]))]
    records = np.column_stack((np.arange(voltage.size), np.full(voltage.size, polarity), voltage, capacity))
    settings = DqdvSettings(2.0, 8.0)
    result = _curve(records, 10, polarity, settings)
    derivative = np.array(result["values"])
    peak = (result["startBin"] + np.argmax(abs(derivative)) + 0.5) * 0.002
    assert peak == pytest.approx(3.65, abs=0.002)
    assert np.all(polarity * derivative >= -1e-9)
    assert abs(0.002 * derivative.sum()) == pytest.approx(capacity[-1], rel=0.001)
    assert abs(0.002 * sum(result["rawValues"])) == pytest.approx(result["capacityMah"])
    assert result["records"][0] < result["records"][-1] if polarity > 0 else result["records"][0] > result["records"][-1]


def test_voltage_repeats_retain_capacity_and_gaps_yield_separate_runs():
    voltage = np.repeat(np.linspace(3.0, 3.10, 51), 3)
    time = np.arange(voltage.size, dtype=float)
    records = np.column_stack((time, np.ones_like(time), voltage, time / 1000))
    result = _curve(records, 1, 1, DqdvSettings())
    assert sum(result["values"]) * 0.002 == pytest.approx(time[-1] / 1000)
    records[70:, 0] += 400
    runs = _continuous_runs(records, 1, 0.002)
    assert [len(run) for run in runs] == [70, 83]


def test_missing_capacity_uses_measured_current_and_time():
    time = np.arange(101, dtype=float)
    records = np.column_stack((time, np.full(time.size, 36), 3.0 + time * 0.002, np.full(time.size, np.nan)))
    result = _curve(records, 1, 1, DqdvSettings())
    assert result["capacitySource"] == "integrated"
    assert result["capacityMah"] == pytest.approx(1)
    records[:, 2] = 3.0
    with pytest.raises(CexError, match="电压采样点太少"):
        _curve(records, 1, 1, DqdvSettings())

import csv

import numpy as np

from xrd_engine.api.commands import Engine
from xrd_engine.electrochem.chi import parse_chi


def test_chi_cv_keeps_sweep_order_and_distinguishes_observed_segments():
    data = parse_chi(b"""Test
Cyclic Voltammetry
Instrument Model: CHI760E
Segment = 8
Results:
Segment 1:
Ep = 1.5V
Potential/V, Current/A
1.0, 1e-3
1.5, 1.5e-3
2.0, 2e-3
1.5, -1e-3
1.0, -2e-3
""")
    assert data.metadata["plannedSegments"] == 8
    assert data.points[:, 0].tolist() == [1, 1.5, 2, 1.5, 1]
    assert data.segments == [{"start": 0, "end": 3, "direction": 1},
                             {"start": 2, "end": 5, "direction": -1}]


def test_chi_fit_exports_out_of_band_measurements_without_predictions(tmp_path):
    frequency = np.geomspace(100_000, 1, 50)
    expected = 2.5 + 44 / (1 + 44 * 0.001 * (2j * np.pi * frequency)**0.83)
    points = [
        f"{f},{z.real},{z.imag},{abs(z)},{np.angle(z, deg=True)}"
        for f, z in zip(frequency, expected, strict=True)
    ]
    points.append("0.1,-8,2,8.246,166")
    source = tmp_path / "called-CV.csv"
    source.write_text(
        "A.C. Impedance\nInstrument Model: CHI760E\n"
        "Freq/Hz, Z'/ohm, Z\"/ohm, Z/ohm, Phase/deg\n" + "\n".join(points),
        encoding="utf-8",
    )
    engine = Engine(tmp_path / "library")

    def call(command, **payload):
        result = engine.handle(command, payload)
        assert result["ok"], result
        return result["data"]

    try:
        imported = call("chi.import", paths=[str(source)])["imported"][0]
        assert imported["kind"] == "EIS"
        identifier = imported["id"]
        assert call("chi.import", paths=[str(source)])["duplicates"][0]["id"] == identifier
        fit = call("chi.fit", id=identifier, model="cpe", min_hz=1, max_hz=100_000, weighting="modulus")
        assert fit["count"] == 50
        assert fit["relativeRmse"] < 0.001
        assert call("chi.open", id=identifier)["fit"]["range"] == [1., 100_000.]
        output_path = tmp_path / "out.csv"
        call("chi.export", id=identifier, path=str(output_path))
        with output_path.open(encoding="utf-8-sig", newline="") as output:
            rows = list(csv.DictReader(line for line in output if not line.startswith("#")))
        assert rows[-1]["real_ohm"] == "-8.0"
        assert rows[-1]["used_in_fit"] == "0"
        assert rows[-1]["fit_real_ohm"] == ""
    finally:
        engine.close()

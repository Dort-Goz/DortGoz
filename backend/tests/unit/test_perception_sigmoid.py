from __future__ import annotations

import numpy as np

from dortgoz.pipeline.perception import _sigmoid


def test_sigmoid_is_stable_at_extremes_and_matches_reference() -> None:
    logits = np.array([[-800.0, -40.0, -1.0, 0.0, 1.0, 40.0, 800.0]], dtype=np.float32)

    with np.errstate(over="raise", under="ignore"):
        out = _sigmoid(logits)

    assert np.isfinite(out).all()
    assert out.min() >= 0.0 and out.max() <= 1.0
    reference = 1.0 / (1.0 + np.exp(-logits[0][2:5].astype(np.float64)))
    assert np.allclose(out[0][2:5], reference, atol=1e-6)
    assert out[0][0] == 0.0
    assert out[0][-1] == 1.0

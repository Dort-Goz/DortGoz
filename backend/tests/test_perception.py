"""Algı katmanı: D-FINE tespiti + pencere özeti (model dosyası yoksa atlanır)."""

import asyncio
from pathlib import Path

import pytest

from dortgoz.config import settings
from dortgoz.pipeline.perception import Detection, WindowPerception, scan_window

MEDIA = Path(__file__).parents[2] / "media"
CLIP = MEDIA / "Stealing095_x264.mp4"

needs_model = pytest.mark.skipif(
    not Path(settings.dfine_onnx).is_file() or not CLIP.is_file(),
    reason="D-FINE ağırlığı ya da örnek klip yok")


def test_iou_geometry():
    a = Detection("person", 0.9, 0.5, 0.5, 0.2, 0.4)
    assert a.iou(a) == pytest.approx(1.0)
    b = Detection("person", 0.9, 0.9, 0.9, 0.1, 0.1)
    assert a.iou(b) == 0.0


def test_meta_text_is_numeric_not_interpretive():
    p = WindowPerception(counts={"person": 2, "car": 1},
                         stationary_persons=1, samples=4)
    text = p.meta_text()
    assert "2 kişi" in text and "1 otomobil" in text
    assert "hareketsiz" in text
    # sınıf sözlüğü dersi: metaya yorum/şüphe dili GİRMEZ
    for banned in ("şüphe", "hırsız", "tehlike", "risk"):
        assert banned not in text.lower()
    empty = WindowPerception(counts={}, stationary_persons=0, samples=4)
    assert empty.hit is False and "görmedi" in empty.meta_text()


@needs_model
def test_scan_window_finds_person_and_car():
    p = asyncio.run(scan_window(CLIP, 0, 25))
    # Stealing095: park halindeki araca yaklaşan kişi — canlı doğrulanmış sahne
    assert p.counts.get("person", 0) >= 1
    assert p.counts.get("car", 0) >= 1
    assert p.hit

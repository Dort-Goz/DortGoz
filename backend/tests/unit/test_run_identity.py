"""Koşu kimliğinin platformlar arası dosya sınırı."""

from pathlib import Path

import pytest

from dortgoz.services.run_identity import require_safe_run_id, safe_run_file


@pytest.mark.parametrize(
    "run_id",
    [
        "analysis-123",
        "canli-KAM_1-1720000000",
        "ithal-deneme.1",
        "Türkçe-01",
    ],
)
def test_safe_run_ids_are_accepted(run_id: str) -> None:
    assert require_safe_run_id(run_id) == run_id


@pytest.mark.parametrize(
    "run_id",
    [
        "../secret",
        r"..\secret",
        r"C:\secret",
        "CON",
        "a/b",
        "a b",
        ".hidden",
        "x:stream",
        "x\x00y",
    ],
)
def test_unsafe_run_ids_are_rejected(run_id: str) -> None:
    with pytest.raises(ValueError):
        require_safe_run_id(run_id)


def test_safe_run_file_stays_under_runs_root(tmp_path: Path) -> None:
    target = safe_run_file(tmp_path / "runs", "analysis-1", ".jsonl")

    assert target == (tmp_path / "runs" / "analysis-1.jsonl").resolve()

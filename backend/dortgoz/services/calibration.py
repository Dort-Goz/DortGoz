from __future__ import annotations

import json
import math
from dataclasses import asdict, dataclass
from pathlib import Path

MIN_PER_CLASS = 3
EPS = 1e-6


@dataclass(frozen=True)
class Calibration:
    a: float
    b: float
    n_pos: int
    n_neg: int
    brier_before: float
    brier_after: float
    logloss_before: float
    logloss_after: float
    fitted_at: float
    model_id: str = ""

    def apply(self, durum_p: float) -> float:
        return _sigmoid(self.a * _logit(durum_p) + self.b)

    def raw_for(self, target: float) -> float | None:
        if self.a <= 0.0 or not 0.0 < target < 1.0:
            return None
        return _sigmoid((_logit(target) - self.b) / self.a)


class NotEnoughLabels(Exception):
    pass


def _logit(p: float) -> float:
    p = min(max(p, EPS), 1.0 - EPS)
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    e = math.exp(z)
    return e / (1.0 + e)


def fit_platt(pairs: list[tuple[float, int]],
              max_iter: int = 100) -> tuple[float, float]:
    pos = sum(1 for _, y in pairs if y == 1)
    neg = len(pairs) - pos
    if pos < MIN_PER_CLASS or neg < MIN_PER_CLASS:
        raise NotEnoughLabels(
            f"her sınıftan en az {MIN_PER_CLASS} etiket gerekir "
            f"(pozitif {pos}, negatif {neg})")

    hi = (pos + 1.0) / (pos + 2.0)
    lo = 1.0 / (neg + 2.0)
    xs = [_logit(p) for p, _ in pairs]
    ts = [hi if y == 1 else lo for _, y in pairs]

    a, b = 0.0, math.log((neg + 1.0) / (pos + 1.0))
    lam = 1e-3

    def loss(a_: float, b_: float) -> float:
        total = 0.0
        for x, t in zip(xs, ts, strict=True):
            z = a_ * x + b_
            total += t * _softplus(-z) + (1 - t) * _softplus(z)
        return total

    current = loss(a, b)
    for _ in range(max_iter):
        h11 = h22 = h21 = g1 = g2 = 0.0
        for x, t in zip(xs, ts, strict=True):
            p = _sigmoid(a * x + b)
            d1, d2 = p - t, p * (1 - p)
            h11 += x * x * d2
            h22 += d2
            h21 += x * d2
            g1 += x * d1
            g2 += d1
        if abs(g1) < 1e-9 and abs(g2) < 1e-9:
            break
        det = (h11 + lam) * (h22 + lam) - h21 * h21
        if abs(det) < 1e-12:
            break
        da = -((h22 + lam) * g1 - h21 * g2) / det
        db = -((h11 + lam) * g2 - h21 * g1) / det

        step = 1.0
        while step >= 1e-10:
            na, nb = a + step * da, b + step * db
            nxt = loss(na, nb)
            if nxt < current + 1e-12:
                a, b, current = na, nb, nxt
                break
            step /= 2.0
        else:
            break
    return a, b


def _softplus(z: float) -> float:
    return math.log1p(math.exp(-abs(z))) + max(z, 0.0)


def _brier(pairs: list[tuple[float, int]]) -> float:
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


def _logloss(pairs: list[tuple[float, int]]) -> float:
    total = 0.0
    for p, y in pairs:
        q = min(max(p, EPS), 1.0 - EPS)
        total -= math.log(q) if y == 1 else math.log(1.0 - q)
    return total / len(pairs)


def calibrate(
    pairs: list[tuple[float, int]], now: float, model_id: str = ""
) -> Calibration:
    a, b = fit_platt(pairs)
    after = [(_sigmoid(a * _logit(p) + b), y) for p, y in pairs]
    return Calibration(
        a=a, b=b,
        n_pos=sum(1 for _, y in pairs if y == 1),
        n_neg=sum(1 for _, y in pairs if y == 0),
        brier_before=_brier(pairs), brier_after=_brier(after),
        logloss_before=_logloss(pairs), logloss_after=_logloss(after),
        fitted_at=now,
        model_id=model_id,
    )


def pairs_from_ledger(path: Path) -> list[tuple[float, int]]:
    latest: dict[str, tuple[float, int]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if row.get("verdict") not in {"anomali", "sorun_degil"}:
            continue
        p = (row.get("signals") or {}).get("durum_p")
        if p is None:
            continue
        key = row.get("key") or row.get("decision_id", "")
        latest[key] = (float(p), 1 if row["verdict"] == "anomali" else 0)
    return list(latest.values())


def save(cal: Calibration, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(asdict(cal), ensure_ascii=False, indent=1),
                    encoding="utf-8")


def load(path: Path, model_id: str = "") -> Calibration | None:
    try:
        calibration = Calibration(**json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError, TypeError):
        return None
    if model_id and calibration.model_id != model_id:
        return None
    return calibration

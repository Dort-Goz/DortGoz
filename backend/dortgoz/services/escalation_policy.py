from __future__ import annotations

from dataclasses import dataclass

from ..config import settings
from . import calibration


@dataclass(frozen=True)
class EscalationThreshold:
    value: float
    source: str
    shadow: bool
    detail: str

    @property
    def acts(self) -> bool:
        return not self.shadow


def resolve() -> EscalationThreshold:
    static = settings.escalate_p
    target = settings.escalate_target_p
    if target <= 0.0:
        return EscalationThreshold(static, "sabit", False,
                                   f"sabit eşik {static:.3f}")

    cal = calibration.load(settings.runs_dir / "kalibrasyon.json")
    if cal is None:
        return EscalationThreshold(
            static, "sabit", False,
            f"kalibrasyon dosyası yok — sabit eşik {static:.3f}")

    raw = cal.raw_for(target)
    if raw is None:
        return EscalationThreshold(
            static, "sabit", False,
            f"kalibrasyon tersine çevrilemedi (A={cal.a:.3f}) — sabit {static:.3f}")

    if raw >= static:
        return EscalationThreshold(
            static, "sabit", False,
            f"kalibre eşik {raw:.4f} sabitten yüksek — YÜKSELTİLMEDİ, "
            f"kurtarma kaybı olmasın diye {static:.3f} korundu")

    shadow = settings.escalate_shadow
    return EscalationThreshold(
        raw, "kalibre", shadow,
        f"kalibre eşik {raw:.4f} (hedef kalibre P={target:.2f}, "
        f"sabit {static:.3f}, {cal.n_pos + cal.n_neg} etiket)"
        + (" — GÖLGE: yalnız kaydedilir" if shadow else ""))

"""Ajan hafızası — olay defteri ve varlık hafızası.

Olay defteri: her olayın yaşam döngüsü (basladi → gelisiyor → sonuclandi)
pencereler arasında takip edilir (şartname: başlangıç/gelişim/sonuç ayrımı).
Varlık hafızası: iz kimlikleri üzerinden kalıcı durum
("3 no'lu kişi 40 sn'dir hareketsiz").
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ..events import Risk


@dataclass
class Incident:
    incident_id: str
    title: str
    first_seen: float
    last_seen: float
    phase: str = "basladi"            # basladi | gelisiyor | sonuclandi
    risk: Risk = "dusuk"
    notes: list[str] = field(default_factory=list)


@dataclass
class Entity:
    track_id: int
    label: str
    first_seen: float
    last_seen: float
    state: str = ""                   # ör. "hareketsiz", "yasak bölgede"


class Ledger:
    def __init__(self) -> None:
        self.incidents: dict[str, Incident] = {}
        self.entities: dict[int, Entity] = {}

    # TODO(hafta 2): pencere raporlarından olay eşleştirme/birleştirme,
    # yaşam döngüsü geçiş kuralları, risk yeniden değerlendirme.

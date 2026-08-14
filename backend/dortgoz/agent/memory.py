"""Ajan hafızası — olay defteri ve varlık hafızası.

Olay defteri: her olayın yaşam döngüsü (basladi → gelisiyor → sonuclandi)
pencereler arasında takip edilir (şartname: başlangıç/gelişim/sonuç ayrımı).
Varlık hafızası: iz kimlikleri üzerinden kalıcı durum
("3 no'lu kişi 40 sn'dir hareketsiz").

## Eşleştirme kuralı (hafta 2 sürümü)

Dedektör/iz kimliği henüz yok, bu yüzden birleştirme **zamansal süreklilik**
üzerinden yapılır: ardışık pencerelerde ciddi olay varsa aynı olayın devamı
sayılır; araya ciddi olay içermeyen bir pencere girerse olay kapanır.
30 sn pencere modeliyle tutarlı ve açıklanabilir.

## `dusuk` olaylar defterе girmez

2026-08-03 ölçümü: normal kliplerin ürettiği 14 olayın tamamı `dusuk` idi
(park eden araç, yürüyen insanlar) — bunlar modelin sahneyi betimlemesi, alarm
değil. `orta`+ eşiğiyle 26 anomali klibinde 20 yakalama ve **0/5 yanlış alarm**.
Defter bu yüzden yalnız `orta` ve üstünü olaya dönüştürür; `dusuk` olanlar
pencere raporunda anlatı olarak kalır.

TODO(hafta 2): iz kimliği gelince varlık hafızası + kimlik tabanlı eşleştirme
TODO(hafta 3): normal-durum kuralı sapmasıyla risk yeniden değerlendirme
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field

from ..events import AnomalyType, IncidentUpdate, Risk, WindowEvent, WindowReport

RISK_ORDER: list[Risk] = ["dusuk", "orta", "yuksek", "kritik"]
ALARM_FLOOR = 1                       # "orta" ve üstü deftere girer


def _rank(risk: Risk) -> int:
    return RISK_ORDER.index(risk)


@dataclass
class Incident:
    incident_id: str
    title: str
    first_seen: float
    last_seen: float
    phase: str = "basladi"            # basladi | gelisiyor | sonuclandi
    anomaly_type: AnomalyType = "bilinmeyen"
    risk: Risk = "dusuk"
    notes: list[str] = field(default_factory=list)
    thumbnail: str | None = None
    # İnsan incelemesi bayrağı: model emin değilse olay operatör kuyruğuna
    # düşer (Bengisu tasarımı: operator_review_required). Gerekçe görünür.
    needs_review: bool = False
    review_reason: str = ""
    # Olay aralığı (jüri metriği/dışa aktarım): doğrulanmış kanıt karelerinin
    # yayılımından türetilir (±1 sn pay) — model sayı tahmininden ÖLÇÜLEBİLİR
    # ölçüde iyi (2026-08-14: IoU@0.5 P 0,097→0,166; model tahmini 3/5 geriletti).
    olay_baslangic: float | None = None
    olay_bitis: float | None = None
    evidence_ts: list[float] = field(default_factory=list)

    def not_evidence(self, events: list) -> None:
        """Ciddi olayların doğrulanmış kanıt zamanlarını toplar, aralığı türetir."""
        for e in events:
            for ref in getattr(e, "evidence", []) or []:
                ts = getattr(ref, "timestamp", None)
                if isinstance(ts, int | float):
                    self.evidence_ts.append(float(ts))
        if self.evidence_ts:
            self.olay_baslangic = max(0.0, min(self.evidence_ts) - 1.0)
            self.olay_bitis = max(self.evidence_ts) + 1.0


@dataclass
class Entity:
    track_id: int
    label: str
    first_seen: float
    last_seen: float
    state: str = ""                   # ör. "hareketsiz", "yasak bölgede"


class Ledger:
    """Pencere raporlarını olaylara dönüştürür ve yaşam döngüsünü yürütür."""

    def __init__(self, grace_windows: int = 1) -> None:
        self.incidents: dict[str, Incident] = {}
        self.entities: dict[int, Entity] = {}
        self._open_id: str | None = None      # süregelen olay (varsa)
        # Tek sessiz pencere olayı KAPATMASIN: uzun bir olayın ortasındaki
        # sınırdaki pencere ("kalabalık masanın etrafında toplanmış") kimi koşuda
        # ciddi olay üretmiyor ve olay ikiye bölünüyordu — süreklilik ipucu bunu
        # azalttı ama koşu varyansı yüzünden tamamen engellemedi (v2 birleşti,
        # v3 yine bölündü). Yapısal çözüm: N sessiz pencere tolere edilir.
        self._grace = grace_windows
        self._quiet = 0

    # ---- sorgu ----

    @property
    def open_incident(self) -> Incident | None:
        return self.incidents.get(self._open_id) if self._open_id else None

    @property
    def quiet_streak(self) -> int:
        """Üst üste kaç sessiz pencere geçti (izleme/hata ayıklama için)."""
        return self._quiet

    @property
    def grace(self) -> int:
        return self._grace

    def serious(self, report: WindowReport) -> list[WindowEvent]:
        return [e for e in report.events if _rank(e.severity_hint) >= ALARM_FLOOR]

    def continuity_hint(self) -> str:
        """Süregelen olayı bir sonraki pencereye taşıyan kısa bağlam metni.

        Pencereler bağımsız yorumlandığı için uzun bir olayın ortasındaki pencere
        "olağan" diyip olayı kapatabiliyordu (ölçüldü 2026-08-05: 270 sn'lik tek
        saldırı defterde İKİ olaya bölündü). Bu ipucu bağlamı taşır.
        ⚠ Çapa etkisine karşı: bittiyse bittiğini söylemesi AÇIKÇA isteniyor —
        yoksa model olay bitse de raporlamayı sürdürür.
        """
        inc = self.open_incident
        if inc is None:
            return ""
        return (
            f"SÜREGELEN OLAY (önceki pencerelerden): {inc.title} — "
            f"{inc.first_seen:.0f}. saniyede başladı, şu ana dek {inc.risk} risk. "
            "Bu pencerede DEVAM EDİYORSA ilgili gözlemleri yine listele "
            "(aynı olayın devamıdır). BİTTİYSE ya da artık görünmüyorsa bunu "
            "açıkça yaz ve olağan olarak işaretle — süren olay yokken olay "
            "uydurma."
        )

    def apply_review(self, incident_id: str, review: dict) -> IncidentUpdate | None:
        """Validated ikinci geçiş anlatısını işler; review/risk fail-closed kalır."""
        inc = self.incidents.get(incident_id)
        if inc is None:
            return None
        was_review_required = inc.needs_review
        previous_review_reason = inc.review_reason
        inc.anomaly_type = review.get("anomaly_type", inc.anomaly_type)
        inc.title = _title_text(review.get("zirve", inc.title))
        if not inc.evidence_ts and \
                isinstance(review.get("baslangic_t"), int | float) and \
                isinstance(review.get("bitis_t"), int | float):
            # Kanıt yayılımı varsa o kazanır; model sayı tahmini yalnız yedek
            inc.olay_baslangic = float(review["baslangic_t"])
            inc.olay_bitis = float(review["bitis_t"])
        # Second-pass VLM risk'i final risk değildir. Ayrıca daha önce verilmiş
        # human-review kararı hiçbir model geçişiyle temizlenemez.
        unc = review.get("belirsizlikler", [])
        inc.needs_review = was_review_required or bool(unc) or inc.anomaly_type == "bilinmeyen"
        if previous_review_reason:
            inc.review_reason = previous_review_reason
        elif unc:
            inc.review_reason = f"2. geçiş: {_short(unc[0])}"
        elif inc.anomaly_type == "bilinmeyen":
            inc.review_reason = "olay kapalı sınıf listesine oturmadı"
        # Yapılandırılmış anlatı — arayüz satır satır gösterir (ok/simge çorbası
        # operatörce okunamıyordu, 2026-08-06 arayüz geri bildirimi)
        detail = "\n".join(filter(None, [
            f"Başlangıç: {review.get('baslangic', '')}".strip(),
            f"Zirve: {review.get('zirve', '')}".strip(),
            f"Sonuç: {review.get('sonuc', '')}".strip(),
            *(f"? {u}" for u in review.get("belirsizlikler", [])[:2]),
        ]))
        return _update(inc, review.get("zirve_t", inc.first_seen), _trim(detail))

    def require_review(
        self,
        reason: str,
        *,
        incident_id: str | None = None,
    ) -> Incident | None:
        """Validation/missing-data nedeniyle review bayrağını sticky olarak ekle."""

        identifier = incident_id or self._open_id
        inc = self.incidents.get(identifier) if identifier is not None else None
        if inc is None:
            return None
        inc.needs_review = True
        normalized = _short(reason.strip()) if reason.strip() else "evidence review gerekli"
        if not inc.review_reason:
            inc.review_reason = normalized
        elif normalized not in inc.review_reason:
            inc.review_reason = _short(f"{inc.review_reason} · {normalized}")
        return inc

    # ---- güncelleme ----

    def ingest(self, report: WindowReport, thumbnail: str | None = None,
               uncertain: str = "") -> list[IncidentUpdate]:
        """Bir pencere raporunu deftere işler; yayınlanacak güncellemeleri döndürür.

        `uncertain` boş değilse pencere GÜVENSİZ kaynaktan geldi (gerekçe metni):
        tırmandırmayla kurtarıldı, model belirsizlik bildirdi vb. — olaya insan
        incelemesi bayrağı olarak işlenir.
        """
        events = self.serious(report)
        if not events:
            if not self._open_id:
                return []
            self._quiet += 1
            if self._quiet > self._grace:
                return self._close()
            return []                     # tolerans içinde: olay açık kalır

        self._quiet = 0
        peak = max(events, key=lambda e: _rank(e.severity_hint))
        current = self.open_incident
        if current is None:
            upd = self._open(peak, events, report, thumbnail)
        else:
            upd = self._extend(current, peak, events, report)
        inc = self.incidents[upd.incident_id]
        self._flag_review(inc, report, uncertain)
        upd.needs_review = inc.needs_review
        upd.review_reason = inc.review_reason
        return [upd]

    def _flag_review(self, inc: Incident, report: WindowReport,
                     uncertain: str) -> None:
        """İnceleme bayrağı kuralları — model 'emin değilim' sinyali verdiyse.

        Kaynaklar: (a) çağıranın işaretlediği güvensiz kaynak (tırmandırma vb.),
        (b) sınıf `bilinmeyen` (kapalı listeye oturmadı), (c) raporun kendi
        `uncertainties` alanı. Bayrak yalnız EKLENİR; kaldırma kararı olay-geneli
        2. geçişindir (apply_review) — pencere pencere yanıp sönmesin.
        """
        reasons = []
        if uncertain:
            reasons.append(uncertain)
        if inc.anomaly_type == "bilinmeyen":
            reasons.append("olay kapalı sınıf listesine oturmadı")
        if report.uncertainties:
            reasons.append("model belirsizlik bildirdi: "
                           + _short(report.uncertainties[0]))
        if reasons and not inc.needs_review:
            inc.needs_review = True
            inc.review_reason = " · ".join(reasons[:2])

    def finalize(self) -> list[IncidentUpdate]:
        """Koşu bittiğinde açık kalan olayı kapatır."""
        return self._close() if self._open_id else []

    # ---- iç geçişler ----

    def _open(self, peak: WindowEvent, events: list[WindowEvent],
              report: WindowReport, thumbnail: str | None) -> IncidentUpdate:
        inc = Incident(
            incident_id=uuid.uuid4().hex[:8],
            title=_title(peak),
            first_seen=peak.t,
            last_seen=events[-1].t,
            phase="basladi",
            anomaly_type=_classify(report),
            risk=peak.severity_hint,
            notes=[e.desc for e in events],
            thumbnail=thumbnail,
        )
        inc.not_evidence(events)
        self.incidents[inc.incident_id] = inc
        self._open_id = inc.incident_id
        return _update(inc, peak.t, report.summary)

    def _extend(self, inc: Incident, peak: WindowEvent, events: list[WindowEvent],
                report: WindowReport) -> IncidentUpdate:
        inc.phase = "gelisiyor"
        inc.last_seen = events[-1].t
        inc.not_evidence(events)
        inc.notes.extend(e.desc for e in events)
        if _rank(peak.severity_hint) > _rank(inc.risk):
            inc.risk = peak.severity_hint          # risk yalnız yukarı revize edilir
            inc.title = _title(peak)               # başlık en ciddi olayı yansıtsın
            inc.anomaly_type = _classify(report)   # sınıf da en ciddi pencereden gelir
        elif inc.anomaly_type == "bilinmeyen":
            inc.anomaly_type = _classify(report)   # sonradan netleşebilir
        return _update(inc, peak.t, report.summary)

    def _close(self) -> list[IncidentUpdate]:
        inc = self.incidents[self._open_id]        # type: ignore[index]
        self._open_id = None
        self._quiet = 0
        inc.phase = "sonuclandi"
        detail = f"{len(inc.notes)} gözlem · {inc.first_seen:.0f}-{inc.last_seen:.0f} sn"
        return [_update(inc, inc.last_seen, detail)]


def _classify(report: WindowReport) -> AnomalyType:
    """Pencerenin sınıfı — ciddi olay varken `normal` gelirse `bilinmeyen`e düşer."""
    return "bilinmeyen" if report.anomaly_type == "normal" else report.anomaly_type


def _title(event: WindowEvent) -> str:
    """Olay başlığı — en ciddi gözlemin ilk cümlesi, kısaltılmış."""
    return _title_text(event.desc)


_TIME_LEAD = re.compile(
    r"^(?:t\s*=\s*\d+(?:[.,]\d+)?\s*s?\s*(?:ile|-|–|ve)?\s*)+"
    r"(?:t\s*=\s*\d+(?:[.,]\d+)?\s*s?\s*)?(?:arasında|civarında|itibarıyla|de|da|'de|'da)?[\s,:-]*",
    re.IGNORECASE)


def _title_text(text: str) -> str:
    """Başlık = ilk cümle, baştaki zaman ifadeleri atılmış hâli.

    2. geçiş anlatısı "t=1147s ile t=1200s arasında zirve yaptı" gibi başlayınca
    kartın başlığı NE olduğunu değil NE ZAMAN olduğunu söylüyordu; saat zaten
    kartın solunda yazıyor (2026-08-05 arayüz geri bildirimi).
    """
    head = _TIME_LEAD.sub("", text.split(".")[0].strip()).strip()
    if head:
        head = head[0].upper() + head[1:]
    return head if len(head) <= 70 else head[:67] + "…"


def _short(text: str, limit: int = 160) -> str:
    """İnceleme gerekçesi tek satır — KELİME sınırında kes (arayüzde 'net değ'
    diye ortadan kesiliyordu, 2026-08-06 ekran görüntüsü)."""
    if len(text) <= limit:
        return text
    cut = text.rfind(" ", 0, limit)
    return text[:cut if cut > limit // 2 else limit].rstrip() + "…"


def _trim(text: str, limit: int = 1200) -> str:
    """Uzun anlatıyı CÜMLE sınırında keser — kelime ortasında kesmek okunmaz
    ('...grubun hala ki' diye bitiyordu, 2026-08-05)."""
    if len(text) <= limit:
        return text
    cut = text.rfind(".", 0, limit)
    return (text[:cut + 1] if cut > limit // 2 else text[:limit].rstrip()) + " …"


def _update(inc: Incident, t: float, detail: str) -> IncidentUpdate:
    return IncidentUpdate(
        incident_id=inc.incident_id,
        t=t,
        phase=inc.phase,                           # type: ignore[arg-type]
        title=inc.title,
        anomaly_type=inc.anomaly_type,
        risk=inc.risk,
        detail=detail,
        thumbnail=inc.thumbnail,
        needs_review=inc.needs_review,
        review_reason=inc.review_reason,
        olay_baslangic=inc.olay_baslangic,
        olay_bitis=inc.olay_bitis,
    )

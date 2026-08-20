"""Analiz paketi: bir koşuyu taşınabilir klasör/zip olarak dışa aktarma ve
içe aktarınca AJAN SOHBETİ DAHİL tam yetenekle geri yükleme.

Paket düzeni (sürüm 1):
    manifest.json      # format sürümü + dosya SHA-256'ları
    analiz.jsonl       # koşunun tam olay akışı (runs/<id>.jsonl kopyası)
    meta.json          # koşu meta'sı (kip, model, istemler)
    ozet.md            # insan-okur özet (uygulamasız da anlamlı)
    video/<ad>         # kaynak video (varsayılan dahil — sohbet araçları
                       # kare/klip üretimi için gerekir)
    kanitlar/t_<sn>.jpg  # kanıt kareleri — videodan yeniden türetilir
                         # (çalışma anındaki kopyalar bilerek geçicidir)

İçe aktarma: video media köküne, JSONL runs/'a alınır; WindowReport'lar ve
defter olayları akıştan yeniden kurulur ve oturum bağlamı kaydedilir —
LangGraph sohbeti içe aktarılan analiz üzerinde aynen çalışır.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import zipfile
from pathlib import Path
from uuid import uuid4

from .. import session
from ..agent.memory import Incident, Ledger
from ..config import settings
from ..events import IncidentUpdate, WindowReport
from ..pipeline.ingest import grab_frame
from ..pipeline.runner import resolve_media
from .analysis_job import iter_run_lines
from .run_identity import require_safe_run_id, safe_run_file

FORMAT_VERSION = 1
_CLOCK = "%02d:%02d"


def _clock(t: float) -> str:
    return _CLOCK % (int(t) // 60, int(t) % 60)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _parse_stream(jsonl: Path) -> tuple[list[WindowReport], dict[str, IncidentUpdate], float]:
    """JSONL → raporlar + olay başına SON güncelleme + süre.

    Yarım kalan son satır paketi erişilemez yapmaz; paylaşılan okuyucu bozuk
    satırı atlar (bkz. `iter_run_lines`).
    """
    reports: list[WindowReport] = []
    incidents: dict[str, IncidentUpdate] = {}
    duration = 0.0
    for envelope in iter_run_lines(jsonl):
        payload = envelope.get("payload", {})
        if not isinstance(payload, dict):
            continue
        kind = payload.get("type")
        if kind == "window_report":
            report = WindowReport.model_validate(payload)
            reports.append(report)
            duration = max(duration, report.window_end)
        elif kind == "incident_update" and payload.get("incident_id"):
            incidents[payload["incident_id"]] = IncidentUpdate.model_validate(payload)
    return reports, incidents, duration


def _package_paths(run_id: str) -> tuple[Path, Path]:
    """(hedef paket, benzersiz .part) — yazım her zaman .part üzerinde olur."""
    require_safe_run_id(run_id)
    out_dir = settings.runs_dir / "paketler"
    out_dir.mkdir(parents=True, exist_ok=True)
    return (out_dir / f"{run_id}.dortgoz.zip",
            out_dir / f"{run_id}.{uuid4().hex}.part")


def _write_package(run_id: str, dest: Path, *, include_video: bool) -> None:
    """Paketi `dest` yoluna yazar (bkz. `export_analysis` — atomik taşıma orada)."""
    jsonl = safe_run_file(settings.runs_dir, run_id, ".jsonl")
    if not jsonl.is_file():
        raise FileNotFoundError(f"koşu bulunamadı: {run_id}")
    meta_path = safe_run_file(settings.runs_dir, run_id, ".meta.json")
    meta = json.loads(meta_path.read_text()) if meta_path.is_file() else {}
    reports, incidents, _ = _parse_stream(jsonl)

    # Kanıt kareleri: akıştaki kanıt zamanlarından yeniden türet
    evidence_ts = sorted({
        round(ref.timestamp, 3)
        for r in reports for e in r.events for ref in e.evidence
    })

    ozet = ["# Dörtgöz analiz paketi", "",
            f"Kayıt: {meta.get('video', '?')} · kip: {meta.get('mode', 'dengeli')}"
            f" · model: {meta.get('model', '?')}", "", "## Olaylar"]
    if incidents:
        for inc in incidents.values():
            aralik = (f"{_clock(inc.olay_baslangic)}–{_clock(inc.olay_bitis)}"
                      if inc.olay_baslangic is not None else f"~{_clock(inc.t)}")
            ozet.append(f"- [{aralik}] {inc.anomaly_type} · risk {inc.risk} · "
                        f"{inc.title}"
                        + (" · **insan incelemesi**" if inc.needs_review else ""))
    else:
        ozet.append("- Olay tespit edilmedi.")

    files: dict[str, str] = {}
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.write(jsonl, "analiz.jsonl")
        files["analiz.jsonl"] = _sha256(jsonl)
        zf.writestr("meta.json", json.dumps(meta, ensure_ascii=False, indent=1))
        zf.writestr("ozet.md", "\n".join(ozet) + "\n")
        video_name = meta.get("video", "")
        if include_video and video_name:
            try:
                src = resolve_media(video_name)
            except Exception:
                # Video media kökü dışında (ör. benchmark koşusu) → videosuz paket;
                # sohbetin metin yeteneği tam kalır, kare üretimi çalışmaz.
                src = None
            if src is not None:
                arc = f"video/{Path(video_name).name}"
                zf.write(src, arc)
                files[arc] = _sha256(src)
        manifest = {"format": "dortgoz-analiz", "surum": FORMAT_VERSION,
                    "run_id": run_id, "video": Path(video_name).name,
                    "kanit_zamanlari": evidence_ts, "sha256": files}
        zf.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=1))


def export_analysis(run_id: str, *, include_video: bool = True) -> Path:
    """Koşuyu zip paketine yazar; paket yolunu döndürür.

    Yazım benzersiz bir `.part` yoluna yapılır ve sonda `os.replace` ile hedefe
    taşınır: eşzamanlı iki dışa aktarma birbirinin yarım zip'ini okuyamaz.
    """
    pkg, part = _package_paths(run_id)
    try:
        _write_package(run_id, part, include_video=include_video)
        os.replace(part, pkg)
    finally:
        part.unlink(missing_ok=True)
    return pkg


async def export_with_evidence(run_id: str, *, include_video: bool = True) -> Path:
    """`export_analysis` + videodan kanıt karelerini pakete ekler (async ffmpeg).

    Kanıt kareleri de `.part` üzerine eklenir; hedefe tek bir taşımayla iner.
    """
    pkg, part = _package_paths(run_id)
    try:
        _write_package(run_id, part, include_video=include_video)
        with zipfile.ZipFile(part) as zf:
            manifest = json.loads(zf.read("manifest.json"))
            meta = json.loads(zf.read("meta.json"))
        video_name = meta.get("video", "")
        src = None
        if video_name and manifest["kanit_zamanlari"]:
            try:
                src = resolve_media(video_name)
            except Exception:
                src = None
        if src is not None:
            with zipfile.ZipFile(part, "a", zipfile.ZIP_DEFLATED) as zf:
                for ts in manifest["kanit_zamanlari"]:
                    try:
                        jpeg = await grab_frame(src, float(ts))
                    except Exception:  # tek kare hatası paketi düşürmez
                        continue
                    zf.writestr(f"kanitlar/t_{ts:.3f}.jpg", jpeg)
        os.replace(part, pkg)
    finally:
        part.unlink(missing_ok=True)
    return pkg


def _rebuild_context(run_id: str, video_rel: str,
                     reports: list[WindowReport],
                     incidents: dict[str, IncidentUpdate],
                     duration: float, feed: str = "") -> session.RunContext:
    ctx = session.start(run_id, video_rel, feed=feed)
    ctx.reports.extend(reports)
    ctx.duration = duration
    ctx.finished = True
    ledger: Ledger = ctx.ledger
    for upd in incidents.values():
        inc = Incident(
            incident_id=upd.incident_id,
            title=upd.title,
            first_seen=upd.olay_baslangic if upd.olay_baslangic is not None else upd.t,
            last_seen=upd.olay_bitis if upd.olay_bitis is not None else upd.t,
            phase=upd.phase,
            anomaly_type=upd.anomaly_type,
            risk=upd.risk,
            notes=[upd.detail] if upd.detail else [],
            thumbnail=upd.thumbnail,
            needs_review=upd.needs_review,
            review_reason=upd.review_reason,
            olay_baslangic=upd.olay_baslangic,
            olay_bitis=upd.olay_bitis,
        )
        ledger.incidents[inc.incident_id] = inc
    return ctx


def import_analysis(package: Path, feed: str = "") -> session.RunContext:
    """Paketi doğrular, yerleştirir ve sohbete hazır oturum bağlamı kurar.

    Dönen bağlamın run_id'si `ithal-` önekiyle yenidir — mevcut koşularla
    çakışmaz; aynı paketin yeniden içe alımı aynı kimliğe biner (idempotent).
    """
    with zipfile.ZipFile(package) as zf:
        names = set(zf.namelist())
        if "manifest.json" not in names or "analiz.jsonl" not in names:
            raise ValueError("geçersiz paket: manifest.json/analiz.jsonl eksik")
        manifest = json.loads(zf.read("manifest.json"))
        if manifest.get("format") != "dortgoz-analiz":
            raise ValueError("geçersiz paket biçimi")
        if manifest.get("surum", 0) > FORMAT_VERSION:
            raise ValueError(f"paket sürümü çok yeni: {manifest.get('surum')}")

        source_id = require_safe_run_id(str(manifest.get("run_id", "analiz")))
        new_id = require_safe_run_id(f"ithal-{source_id}"[:48])
        jsonl_dst = safe_run_file(settings.runs_dir, new_id, ".jsonl")
        jsonl_dst.parent.mkdir(parents=True, exist_ok=True)
        analysis_bytes = zf.read("analiz.jsonl")
        expected_analysis_hash = manifest.get("sha256", {}).get("analiz.jsonl")
        if expected_analysis_hash and hashlib.sha256(analysis_bytes).hexdigest() != expected_analysis_hash:
            raise ValueError("analiz.jsonl sağlama toplamı tutmuyor")
        jsonl_dst.write_bytes(analysis_bytes)
        if "meta.json" in names:
            safe_run_file(settings.runs_dir, new_id, ".meta.json").write_bytes(
                zf.read("meta.json")
            )

        video_rel = ""
        video_members = [n for n in names if n.startswith("video/") and not n.endswith("/")]
        if video_members:
            member = video_members[0]
            video_rel = f"ithal_{Path(member).name}"
            dst = settings.media_dir / video_rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(member) as src, dst.open("wb") as out:
                shutil.copyfileobj(src, out)

    reports, incidents, duration = _parse_stream(jsonl_dst)
    return _rebuild_context(new_id, video_rel, reports, incidents, duration, feed=feed)

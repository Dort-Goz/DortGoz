"""Sadece yerel, hash-doğrulanmış prosedür manifest index'i."""

from __future__ import annotations

import hashlib
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from ..domain.event import RiskLevel
from ..domain.evidence import VerifiedEventType
from ..domain.provenance import ProcedureSource


class ProcedureSection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    section: str = Field(min_length=1)
    action: str = Field(min_length=1)


class ProcedureDocument(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    document_id: str = Field(min_length=1)
    path: str = Field(min_length=1)
    version: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    valid_from: date | None = None
    valid_until: date | None = None
    approved_for_demo: bool = False
    event_types: list[VerifiedEventType] = Field(default_factory=list)
    risk_levels: list[RiskLevel] = Field(default_factory=list)
    sections: list[ProcedureSection] = Field(min_length=1)


class ProcedureManifest(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: str = Field(min_length=1)
    documents: list[ProcedureDocument] = Field(default_factory=list)


class LocalProcedureIndex:
    def __init__(self, root: Path, manifest: ProcedureManifest) -> None:
        self.root = root.resolve()
        self.manifest = manifest

    @classmethod
    def load(cls, root: Path, manifest_path: Path) -> LocalProcedureIndex:
        try:
            manifest = ProcedureManifest.model_validate_json(manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError("procedure manifest geçersiz") from exc
        index = cls(root, manifest)
        for document in manifest.documents:
            index._verify(document)
        return index

    def find(self, event_type: VerifiedEventType, risk_level: RiskLevel, *, on_date: date | None = None) -> list[tuple[ProcedureDocument, ProcedureSection, ProcedureSource]]:
        today = on_date or date.today()
        matches = []
        for document in self.manifest.documents:
            if not document.approved_for_demo or not self._is_current(document, today):
                continue
            if event_type not in document.event_types or risk_level not in document.risk_levels:
                continue
            source_base = dict(document_id=document.document_id, version=document.version, valid_from=document.valid_from, content_hash=document.content_hash)
            matches.extend((document, section, ProcedureSource(section=section.section, **source_base)) for section in document.sections)
        return matches

    def _verify(self, document: ProcedureDocument) -> None:
        target = (self.root / document.path).resolve()
        if not target.is_relative_to(self.root) or not target.is_file():
            raise ValueError(f"procedure document bulunamadı: {document.document_id}")
        if _hash(target) != document.content_hash:
            raise ValueError(f"procedure document hash uyuşmuyor: {document.document_id}")

    @staticmethod
    def _is_current(document: ProcedureDocument, current: date) -> bool:
        return (document.valid_from is None or document.valid_from <= current) and (document.valid_until is None or current <= document.valid_until)


def _hash(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["LocalProcedureIndex", "ProcedureDocument", "ProcedureManifest", "ProcedureSection"]

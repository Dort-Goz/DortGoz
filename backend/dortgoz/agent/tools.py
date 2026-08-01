"""Ajan araç kaydı — sensörler, aktüatörler (mock) ve arayüz araçları.

Şartname gereği aktüatörler mock fonksiyonlardır; her çağrı gerekçesiyle
birlikte olay akışına yazılır (açıklanabilirlik). Kritik aktüatörler
operatör onayı ister (human-in-the-loop → ActuatorRequest).

Arayüz araçları ajanın konsolu yönlendirmesini sağlar: "00:15'teki olayı
göster" → seek_video + highlight_incident.
"""

from __future__ import annotations

import uuid

from ..events import ActuatorRequest, Event, ToolCall, UICommand
from ..ws import ConnectionManager

# Onay gerektiren kritik aktüatörler
CRITICAL = {"alarm_ver", "alan_kapat"}


async def call_actuator(manager: ConnectionManager, name: str, reason: str,
                        incident_id: str | None = None) -> None:
    await manager.broadcast(Event.wrap(ToolCall(tool=name, rationale=reason)))
    if name in CRITICAL:
        await manager.broadcast(Event.wrap(ActuatorRequest(
            request_id=uuid.uuid4().hex[:8],
            actuator=name, reason=reason, incident_id=incident_id,
        )))
    # Onay gerektirmeyenler doğrudan "çalıştırılmış" sayılır (mock).


async def ui_seek(manager: ConnectionManager, t: float) -> None:
    await manager.broadcast(Event.wrap(UICommand(action="seek_video", args={"t": t})))


async def ui_highlight(manager: ConnectionManager, incident_id: str) -> None:
    await manager.broadcast(Event.wrap(UICommand(
        action="highlight_incident", args={"incident_id": incident_id})))


# TODO(hafta 3): sensör araçları — query_detections(window), request_burst_analysis(t),
# lookup_procedure(event_type) [normal-durum kuralları + prosedür RAG].

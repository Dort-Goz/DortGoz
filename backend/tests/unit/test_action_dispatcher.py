import json

import pytest

from dortgoz import session
from dortgoz.agent.memory import Incident
from dortgoz.events import ActuatorRequest, Event, IncidentUpdate, RunStatus
from dortgoz.services import triage
from dortgoz.services.action_dispatcher import ActionDispatcher


@pytest.fixture(autouse=True)
def clean_runtime(monkeypatch):
    test_store = triage.TriageStore(allow_ledger_only=True)
    monkeypatch.setattr(triage, "store", test_store)
    session.clear()
    triage.store.clear()
    yield
    session.clear()
    triage.store.clear()


def incident_context(
    *,
    risk="yuksek",
    anomaly_type="kavga",
    evidence=(12.5, 13.0),
    needs_review=False,
):
    ctx = session.start("run-1", "crime.mp4", feed="KAM-1")
    incident = Incident(
        incident_id="inc-1",
        title="Kavga şüphesi",
        first_seen=12.5,
        last_seen=13.0,
        risk=risk,
        anomaly_type=anomaly_type,
        evidence_ts=list(evidence),
        needs_review=needs_review,
    )
    ctx.ledger.incidents[incident.incident_id] = incident
    return ctx, incident


def confirm_incident(incident: Incident, *, live: bool = False) -> None:
    triage.store.observe(Event.wrap(
        RunStatus(run_id="run-1", state="done", video="crime.mp4"),
        feed="KAM-1",
        live=live,
    ))
    triage.store.observe(Event.wrap(
        IncidentUpdate(
            incident_id=incident.incident_id,
            t=incident.first_seen,
            phase="sonuclandi",
            title=incident.title,
            anomaly_type=incident.anomaly_type,
            risk=incident.risk,
            needs_review=True,
        ),
        feed="KAM-1",
        live=live,
    ))
    triage.store.decide(
        "KAM-1:inc-1",
        "anomali",
        category=incident.anomaly_type,
        reviewer="Operatör 1",
    )


def test_request_is_bound_to_real_incident_and_evidence(tmp_path):
    incident_context()
    service = ActionDispatcher(tmp_path)

    request, created = service.request(
        "emniyet_bildirimi_hazirla",
        "inc-1",
        "KAM-1",
        "Yüksek riskli kavga",
    )

    assert created is True
    assert request.run_id == "run-1"
    assert request.feed == "KAM-1"
    assert request.live is False
    assert request.anomaly_type == "kavga"
    assert request.risk == "yuksek"
    assert request.evidence_timestamps == [12.5, 13.0]
    assert request.mode == "preview"
    assert (tmp_path / "aksiyonlar" / "aksiyon_defteri.jsonl").is_file()


def test_live_request_and_result_keep_workspace_identity(tmp_path):
    _, incident = incident_context(needs_review=True)
    confirm_incident(incident, live=True)
    service = ActionDispatcher(tmp_path)

    request, _ = service.request(
        "emniyet_bildirimi_hazirla", "inc-1", "KAM-1", "canlı olay"
    )
    result = service.resolve(request.request_id, False, "Operatör 1")

    assert request.live is True
    assert result.live is True
    assert service.snapshot()["requests"][0]["live"] is True


def test_duplicate_pending_request_is_idempotent(tmp_path):
    incident_context()
    service = ActionDispatcher(tmp_path)

    first, first_created = service.request(
        "emniyet_bildirimi_hazirla", "inc-1", "KAM-1", "ilk gerekçe"
    )
    second, second_created = service.request(
        "emniyet_bildirimi_hazirla", "inc-1", "KAM-1", "ikinci gerekçe"
    )

    assert first_created is True
    assert second_created is False
    assert second.request_id == first.request_id


def test_approval_writes_local_preview_without_delivery(tmp_path):
    incident_context()
    service = ActionDispatcher(tmp_path)
    request, _ = service.request(
        "emniyet_bildirimi_hazirla", "inc-1", "KAM-1", "kanıta dayalı gerekçe"
    )

    result = service.resolve(request.request_id, True, "Operatör 1")

    assert result.status == "prepared"
    assert result.delivered is False
    assert result.external_side_effect is False
    assert result.artifact_url == f"/api/actions/{request.request_id}/artifact"
    markdown = service.artifact(request.request_id).read_text(encoding="utf-8")
    assert "Dış kuruma iletilmedi" in markdown
    payload = json.loads(
        (tmp_path / "aksiyonlar" / request.request_id / "bildirim_taslagi.json")
        .read_text(encoding="utf-8")
    )
    assert payload["delivery"] == {
        "mode": "preview",
        "delivered": False,
        "external_side_effect": False,
    }


def test_ui_fixture_uses_same_local_preview_and_stays_separate(tmp_path):
    service = ActionDispatcher(tmp_path)
    request, created = service.register_ui_fixture(ActuatorRequest(
        request_id="fixture-req-demo123",
        actuator="emniyet_bildirimi_hazirla",
        reason="Arayüz onay akışını sınar",
        incident_id="FIX-INC-001",
        incident_title="Zorla giriş şüphesi",
        run_id="fixture-ui-crime-demo123",
        feed="KAM-TEST",
        anomaly_type="hirsizlik",
        risk="yuksek",
        evidence_timestamps=[12.0],
    ))

    result = service.resolve(request.request_id, True, "Operatör 1")

    assert created is True
    assert result.status == "prepared"
    assert result.delivered is False
    assert result.external_side_effect is False
    assert service.artifact(request.request_id).is_file()
    assert service.snapshot(fixture_only=True)["requests"][0]["request_id"] == request.request_id
    assert service.snapshot(fixture_only=False)["requests"] == []


def test_ui_fixture_registration_rejects_real_run_id(tmp_path):
    service = ActionDispatcher(tmp_path)
    with pytest.raises(ValueError, match="fixture koşusuna"):
        service.register_ui_fixture(ActuatorRequest(
            request_id="fixture-req-demo123",
            actuator="emniyet_bildirimi_hazirla",
            reason="geçersiz",
            incident_id="inc-1",
            run_id="real-run",
            anomaly_type="hirsizlik",
            risk="yuksek",
            evidence_timestamps=[12.0],
        ))


def test_resolution_is_restart_safe_and_conflicts_are_rejected(tmp_path):
    incident_context()
    service = ActionDispatcher(tmp_path)
    request, _ = service.request(
        "emniyet_bildirimi_hazirla", "inc-1", "KAM-1", "gerekçe"
    )
    first = service.resolve(request.request_id, False, "Operatör 1")

    restarted = ActionDispatcher(tmp_path)
    repeated = restarted.resolve(request.request_id, False, "Operatör 1")

    assert repeated == first
    with pytest.raises(ValueError, match="çelişkili"):
        restarted.resolve(request.request_id, True, "Operatör 1")


def test_legacy_live_record_is_migrated_from_run_id(tmp_path):
    root = tmp_path / "aksiyonlar"
    root.mkdir(parents=True)
    request = ActuatorRequest(
        request_id="legacy-live",
        actuator="emniyet_bildirimi_hazirla",
        reason="eski canlı kayıt",
        incident_id="inc-live",
        run_id="canli-KAM-1-seg_001",
        feed="KAM-1",
        anomaly_type="hirsizlik",
        risk="yuksek",
        evidence_timestamps=[5.0],
    ).model_dump(mode="json")
    request.pop("live")
    (root / "aksiyon_defteri.jsonl").write_text(
        json.dumps({"version": 1, "request": request, "result": None}) + "\n",
        encoding="utf-8",
    )

    snapshot = ActionDispatcher(tmp_path).snapshot()

    assert snapshot["requests"][0]["live"] is True


def test_artifact_route_does_not_trust_persisted_file_path(tmp_path):
    incident_context()
    service = ActionDispatcher(tmp_path)
    request, _ = service.request(
        "emniyet_bildirimi_hazirla", "inc-1", "KAM-1", "gerekçe"
    )
    service.resolve(request.request_id, True, "Operatör 1")
    secret = tmp_path / "secret.txt"
    secret.write_text("gizli", encoding="utf-8")
    ledger = tmp_path / "aksiyonlar" / "aksiyon_defteri.jsonl"
    rows = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
    rows[-1]["artifact_path"] = str(secret)
    ledger.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    path = ActionDispatcher(tmp_path).artifact(request.request_id)

    assert path.name == "bildirim_ozeti.md"
    assert path != secret


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"evidence": ()}, "kanıt"),
        ({"risk": "dusuk"}, "risk"),
        ({"anomaly_type": "yangin"}, "uygun değil"),
    ],
)
def test_unsafe_incidents_do_not_open_action(tmp_path, changes, message):
    incident_context(**changes)
    service = ActionDispatcher(tmp_path)

    with pytest.raises(ValueError, match=message):
        service.request(
            "emniyet_bildirimi_hazirla", "inc-1", "KAM-1", "gerekçe"
        )


def test_review_required_incident_needs_operator_confirmation(tmp_path):
    _, incident = incident_context(needs_review=True)
    service = ActionDispatcher(tmp_path)

    with pytest.raises(ValueError, match="insan incelemesi"):
        service.request(
            "emniyet_bildirimi_hazirla", "inc-1", "KAM-1", "gerekçe"
        )

    confirm_incident(incident)
    request, created = service.request(
        "emniyet_bildirimi_hazirla", "inc-1", "KAM-1", "gerekçe"
    )
    assert created is True
    assert request.incident_id == "inc-1"


def test_confirmed_incident_exposes_only_eligible_local_draft_suggestions(tmp_path):
    _, incident = incident_context(
        risk="yuksek",
        anomaly_type="hirsizlik",
        needs_review=True,
    )
    confirm_incident(incident)
    service = ActionDispatcher(tmp_path)

    suggestions = service.suggestions("KAM-1", "inc-1")

    assert {item["action"] for item in suggestions} == {
        "emniyet_bildirimi_hazirla",
        "guvenlik_uyarisi_hazirla",
        "alan_guvenligi_iste",
    }
    assert all(item["status"] == "available" for item in suggestions)
    assert all(item["request_id"] is None for item in suggestions)


def test_wrong_feed_and_unknown_request_are_rejected(tmp_path):
    incident_context()
    service = ActionDispatcher(tmp_path)

    with pytest.raises(ValueError, match="olay bulunamadı"):
        service.request(
            "emniyet_bildirimi_hazirla", "inc-1", "KAM-2", "gerekçe"
        )
    with pytest.raises(KeyError, match="bulunamadı"):
        service.resolve("missing", True)


@pytest.mark.asyncio
async def test_local_incident_report_uses_completed_real_run(monkeypatch, tmp_path):
    ctx, _ = incident_context()
    ctx.finished = True
    report_path = tmp_path / "run-1.zip"
    report_path.write_bytes(b"report")

    async def fake_export(run_id):
        assert run_id == "run-1"
        return report_path

    monkeypatch.setattr(
        "dortgoz.services.analysis_package.export_with_evidence",
        fake_export,
    )
    service = ActionDispatcher(tmp_path)

    path, url = await service.create_report("KAM-1", "inc-1")

    assert path == report_path
    assert url == "/api/runs/run-1/export"

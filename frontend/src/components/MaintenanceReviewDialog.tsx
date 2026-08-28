import { useEffect, useMemo, useState } from "react";
import {
  getCanonicalEvent,
  getEventReviews,
  getIncidentMedia,
  saveMaintenanceReview,
} from "../lib/api";
import { CANONICAL_TYPE_TR, RISK_TR } from "../lib/labels";
import type { CanonicalEvent, HumanReview, IncidentMedia } from "../types/domain";
import type { CanonicalEventType, Risk } from "../types/events";

const EVENT_TYPES: CanonicalEventType[] = [
  "physical_fight",
  "assault",
  "possible_theft",
  "possible_armed_incident",
  "fire_smoke",
  "explosion",
  "vehicle_collision",
  "vandalism",
  "unknown_anomaly",
];

const RISKS: Risk[] = ["dusuk", "orta", "yuksek", "kritik"];

const DECISION_TR = {
  confirm: "Anomali doğru",
  edit: "Anomali düzeltildi",
  reject: "Anomali yok",
} as const;

type ItVerdict = "correct" | "edit" | "none";

function categoryLabel(value: string | null | undefined): string {
  if (!value) return "—";
  return CANONICAL_TYPE_TR[value as CanonicalEventType] ?? value;
}

function clock(value: number | null): string {
  if (value === null) return "—";
  const minutes = Math.floor(value / 60);
  const seconds = value - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${seconds.toFixed(1).padStart(4, "0")}`;
}

export default function MaintenanceReviewDialog({
  eventId,
  user,
  onClose,
  onSaved,
}: {
  eventId: string;
  user: string;
  onClose: () => void;
  onSaved: (eventId: string) => void;
}) {
  const [event, setEvent] = useState<CanonicalEvent | null>(null);
  const [operatorReview, setOperatorReview] = useState<HumanReview | null>(null);
  const [media, setMedia] = useState<IncidentMedia | null>(null);
  const [verdict, setVerdict] = useState<ItVerdict>("correct");
  const [eventType, setEventType] = useState<CanonicalEventType>("unknown_anomaly");
  const [risk, setRisk] = useState<Risk>("orta");
  const [start, setStart] = useState(0);
  const [peak, setPeak] = useState(0);
  const [end, setEnd] = useState(0);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      getCanonicalEvent(eventId),
      getEventReviews(eventId),
      getIncidentMedia(eventId),
    ]).then(([canonical, reviews, incidentMedia]) => {
      if (cancelled) return;
      const latest = reviews.at(-1) ?? null;
      setEvent(canonical);
      setOperatorReview(latest);
      setMedia(incidentMedia);
      const selectedType = latest?.event_type ?? canonical.event_type;
      setEventType(
        EVENT_TYPES.includes(selectedType as CanonicalEventType)
          ? selectedType as CanonicalEventType
          : "unknown_anomaly",
      );
      setRisk(
        RISKS.includes(latest?.risk_level as Risk)
          ? latest?.risk_level as Risk
          : "orta",
      );
      const initialStart = latest?.start_time ?? canonical.start_time ?? 0;
      const initialEnd = latest?.end_time ?? canonical.end_time ?? initialStart;
      setStart(initialStart);
      setPeak(latest?.peak_time ?? canonical.peak_time ?? (initialStart + initialEnd) / 2);
      setEnd(initialEnd);
    }).catch((reason) => {
      if (!cancelled) {
        setError(reason instanceof Error ? reason.message : "IT incelemesi açılamadı.");
      }
    });
    return () => {
      cancelled = true;
    };
  }, [eventId]);

  const validTimes = start <= peak && peak <= end;
  const canSave = Boolean(
    operatorReview && user.trim() && note.trim() && (verdict === "none" || validTimes),
  );
  const operatorFacts = useMemo(() => {
    if (!operatorReview) return [];
    return [
      DECISION_TR[operatorReview.decision],
      categoryLabel(operatorReview.event_type),
      operatorReview.risk_level
        ? RISK_TR[operatorReview.risk_level as Risk] ?? operatorReview.risk_level
        : "—",
      `${clock(operatorReview.start_time)}–${clock(operatorReview.end_time)}`,
    ];
  }, [operatorReview]);

  const save = async () => {
    if (!operatorReview || !canSave) return;
    setBusy(true);
    setError("");
    try {
      await saveMaintenanceReview(eventId, {
        operator_review_id: operatorReview.review_id,
        decision: verdict === "none" ? "reject" : verdict === "edit" ? "edit" : "confirm",
        reviewer: user.trim(),
        note: note.trim(),
        ...(verdict === "none"
          ? { false_alarm_reason: "normal_activity" }
          : {
              event_type: eventType,
              risk_level: risk,
              start_time: start,
              peak_time: peak,
              end_time: end,
            }),
      });
      onSaved(eventId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "IT incelemesi kaydedilemedi.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/75 p-4">
      <section
        role="dialog"
        aria-modal="true"
        aria-label="IT incelemesi"
        className="flex max-h-[94vh] w-full max-w-5xl flex-col overflow-hidden rounded-md border border-zinc-800 bg-zinc-950 shadow-2xl"
      >
        <header className="flex items-center border-b border-zinc-800 px-4 py-3">
          <h2 className="text-sm font-semibold text-zinc-100">IT İncelemesi</h2>
          <button type="button" onClick={onClose} className="btn btn-ghost ml-auto">
            Kapat ×
          </button>
        </header>

        <div className="min-h-0 flex-1 overflow-y-auto p-4 text-xs">
          {error && (
            <div className="mb-3 border border-red-900 bg-red-950/40 px-3 py-2 text-red-200">
              {error}
            </div>
          )}

          <section className="border border-zinc-700 bg-zinc-900 p-3">
            <div className="microlabel mb-2">Operatör kararı</div>
            <div className="flex flex-wrap gap-2">
              {operatorFacts.map((fact, index) => (
                <span key={`${fact}-${index}`} className="chip border border-zinc-700 bg-zinc-950 text-zinc-200">
                  {fact}
                </span>
              ))}
            </div>
            {operatorReview?.note && (
              <p className="mt-2 whitespace-pre-wrap text-zinc-300">{operatorReview.note}</p>
            )}
          </section>

          <div className="mt-3 grid gap-3 lg:grid-cols-[minmax(18rem,1fr)_minmax(22rem,1fr)]">
            <section className="overflow-hidden border border-zinc-800 bg-black">
              {media ? (
                <video
                  key={media.clip_url}
                  controls
                  preload="metadata"
                  poster={media.thumbnail_url}
                  src={media.clip_url}
                  className="aspect-video h-full max-h-[28rem] w-full object-contain"
                />
              ) : (
                <div className="flex aspect-video items-center justify-center text-zinc-600">
                  Görüntü yok
                </div>
              )}
            </section>

            <section className="space-y-3 border border-zinc-800 bg-zinc-900 p-3">
              <div className="grid grid-cols-1 gap-2 sm:grid-cols-3">
                {([
                  ["correct", "Anomali doğru"],
                  ["edit", "Kategori değiştir"],
                  ["none", "Anomali yok"],
                ] as const).map(([value, label]) => (
                  <button
                    key={value}
                    type="button"
                    onClick={() => setVerdict(value)}
                    className={`btn btn-wrap ${verdict === value ? "btn-accent" : "btn-outline"}`}
                  >
                    {label}
                  </button>
                ))}
              </div>

              {verdict !== "none" && (
                <>
                  <div className="grid gap-2 sm:grid-cols-2">
                    <label className="space-y-1">
                      <span className="microlabel block">kategori</span>
                      <select
                        value={eventType}
                        onChange={(change) => setEventType(change.target.value as CanonicalEventType)}
                        className="field w-full"
                        disabled={verdict === "correct"}
                      >
                        {EVENT_TYPES.map((type) => (
                          <option key={type} value={type}>{categoryLabel(type)}</option>
                        ))}
                      </select>
                    </label>
                    <label className="space-y-1">
                      <span className="microlabel block">risk</span>
                      <select
                        value={risk}
                        onChange={(change) => setRisk(change.target.value as Risk)}
                        className="field w-full"
                      >
                        {RISKS.map((item) => (
                          <option key={item} value={item}>{RISK_TR[item]}</option>
                        ))}
                      </select>
                    </label>
                  </div>
                  <div className="grid grid-cols-3 gap-2">
                    {([
                      ["başlangıç", start, setStart],
                      ["zirve", peak, setPeak],
                      ["bitiş", end, setEnd],
                    ] as const).map(([label, value, setter]) => (
                      <label key={label} className="space-y-1">
                        <span className="microlabel block">{label}</span>
                        <input
                          type="number"
                          min={0}
                          step={0.1}
                          value={value}
                          onChange={(change) => setter(Number(change.target.value))}
                          className="field w-full"
                        />
                      </label>
                    ))}
                  </div>
                  {!validTimes && <p className="text-red-300">Zaman sırası geçersiz.</p>}
                </>
              )}

              <label className="space-y-1">
                <span className="microlabel block">IT notu</span>
                <textarea
                  value={note}
                  onChange={(change) => setNote(change.target.value)}
                  rows={3}
                  className="field w-full resize-y"
                />
              </label>

              <button
                type="button"
                disabled={!canSave || busy}
                onClick={() => void save()}
                className="btn btn-accent btn-wrap w-full"
              >
                {busy ? "Kaydediliyor…" : "IT incelemesini kaydet"}
              </button>
            </section>
          </div>

          {!event && !error && <p className="py-8 text-center text-zinc-500">Yükleniyor…</p>}
        </div>
      </section>
    </div>
  );
}

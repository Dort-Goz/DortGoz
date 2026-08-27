import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  ApiError,
  approveEventForLearning,
  getCanonicalEvent,
  getDevelopmentApprovals,
  getEventReviews,
  getIncidentMedia,
  getLearningPlan,
  getTrainingSamples,
  prepareTrainingSamples,
  revokeEventLearningApproval,
  saveEventReview,
  verifyTrainingSample,
} from "../lib/api";
import { CANONICAL_TYPE_TR, RISK_TR } from "../lib/labels";
import {
  operatorApprovalState,
  presentationForUse,
  systemBehaviorLabel,
} from "../lib/learningPresentation";
import { boxFromPoints, imagePoint, type ImagePoint } from "../lib/trainingBoxes";
import type {
  CanonicalEvent,
  DevelopmentApproval,
  DevelopmentUse,
  HumanReview,
  IncidentMedia,
  LearningPlan,
  TrainingSample,
  VerifiedBoundingBox,
} from "../types/domain";
import type { CanonicalEventType, Risk } from "../types/events";

const STATUS_TR = {
  pending_review: "İnceleme bekliyor",
  verified: "Doğrulandı",
  revoked: "Geçersiz",
} as const;

const REASON_TR: Record<string, string> = {
  event_start: "Başlangıç",
  event_peak: "Zirve",
  event_end: "Bitiş",
  operator_selected: "Operatör seçimi",
};

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

const FALSE_ALARM_TR: Record<string, string> = {
  normal_activity: "Olağan hareket",
  camera_condition: "Kamera veya ışık koşulu",
  occlusion: "Görüş engeli",
  reflection_or_shadow: "Yansıma veya gölge",
  duplicate_event: "Aynı olayın tekrarı",
  wrong_classification: "Yanlış sınıflandırma",
  other: "Diğer",
};

const REVIEW_DECISION_TR = {
  confirm: "Doğrulandı",
  edit: "Düzeltildi",
  reject: "Sorun değil",
} as const;

const APPROVAL_STATUS_TR = {
  approved: "Onaylandı",
  rejected: "Reddedildi",
  revoked: "Geri alındı",
} as const;

const dateTime = (value: string) => new Date(value).toLocaleString("tr-TR", {
  dateStyle: "short",
  timeStyle: "short",
});

const clock = (seconds: number) => {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds - minutes * 60;
  return `${String(minutes).padStart(2, "0")}:${rest.toFixed(1).padStart(4, "0")}`;
};

function messageOf(error: unknown): string {
  if (error instanceof ApiError && error.code === "TRAINING_MEDIA_MISSING") {
    return "Video bu bilgisayarda yok. Bu adımı videonun bulunduğu bilgisayarda çalıştırın.";
  }
  if (error instanceof ApiError && error.code === "TRAINING_MANIFEST_NOT_FOUND") {
    return "Eğitim manifesti bulunamadı. runs/datasets içindeki dosya adını kontrol edin.";
  }
  if (error instanceof ApiError && error.code === "TRAINING_DATASET_REJECTED") {
    return "Bu veri seti eğitime kapalı. Lisans doğrulanmadan kare hazırlanamaz.";
  }
  return error instanceof Error ? error.message : "İşlem tamamlanamadı.";
}

function BoxEditor({
  sample,
  boxes,
  onBoxes,
}: {
  sample: TrainingSample;
  boxes: VerifiedBoundingBox[];
  onBoxes: (boxes: VerifiedBoundingBox[]) => void;
}) {
  const [category, setCategory] = useState("person");
  const [draft, setDraft] = useState<{ start: ImagePoint; current: ImagePoint } | null>(null);
  const disabled = sample.status !== "pending_review";

  const point = (event: ReactPointerEvent<SVGSVGElement>) => {
    const bounds = event.currentTarget.getBoundingClientRect();
    return imagePoint(
      event.clientX,
      event.clientY,
      bounds,
      sample.image_width,
      sample.image_height,
    );
  };

  const start = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (disabled || event.button !== 0) return;
    event.currentTarget.setPointerCapture(event.pointerId);
    const startPoint = point(event);
    setDraft({ start: startPoint, current: startPoint });
  };

  const move = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (draft === null || disabled) return;
    setDraft({ ...draft, current: point(event) });
  };

  const finish = (event: ReactPointerEvent<SVGSVGElement>) => {
    if (draft === null || disabled) return;
    const geometry = boxFromPoints(draft.start, point(event));
    setDraft(null);
    if (geometry === null) return;
    if (!/^[a-z][a-z0-9_-]{0,63}$/.test(category)) return;
    onBoxes([...boxes, { category_name: category, ...geometry, is_crowd: false }]);
  };

  const draftBox = draft ? boxFromPoints(draft.start, draft.current, 0) : null;

  return (
    <div className="space-y-2">
      <div className="relative overflow-hidden rounded-md border border-zinc-800 bg-black">
        <img src={sample.frame_url} alt="Eğitim karesi" className="block w-full select-none" />
        <svg
          viewBox={`0 0 ${sample.image_width} ${sample.image_height}`}
          className={`absolute inset-0 h-full w-full ${disabled ? "" : "cursor-crosshair"}`}
          style={{ touchAction: "none" }}
          onPointerDown={start}
          onPointerMove={move}
          onPointerUp={finish}
          onPointerCancel={() => setDraft(null)}
        >
          {boxes.map((box, index) => (
            <g key={`${box.category_name}-${index}`}>
              <rect
                x={box.x}
                y={box.y}
                width={box.width}
                height={box.height}
                fill="rgba(16,185,129,0.12)"
                stroke="#34d399"
                strokeWidth="2"
                vectorEffect="non-scaling-stroke"
              />
              <text
                x={box.x + 3}
                y={Math.max(14, box.y + 14)}
                fill="#ecfdf5"
                fontSize="12"
                paintOrder="stroke"
                stroke="#064e3b"
                strokeWidth="3"
              >
                {box.category_name}
              </text>
            </g>
          ))}
          {draftBox && (
            <rect
              {...draftBox}
              fill="rgba(56,189,248,0.12)"
              stroke="#38bdf8"
              strokeWidth="2"
              strokeDasharray="6 4"
              vectorEffect="non-scaling-stroke"
            />
          )}
        </svg>
      </div>

      {!disabled && (
        <div className="flex items-center gap-2 text-xs">
          <label className="text-zinc-400">Nesne sınıfı</label>
          <input
            value={category}
            onChange={(event) => setCategory(event.target.value.toLowerCase())}
            className="field w-36 font-mono"
            placeholder="person"
          />
          <span className="text-zinc-500">Görüntü üzerinde sürükleyerek kutu çizin.</span>
        </div>
      )}

      {boxes.length > 0 && (
        <div className="flex flex-wrap gap-1 text-xs">
          {boxes.map((box, index) => (
            <button
              key={`${box.category_name}-${index}`}
              type="button"
              disabled={disabled}
              onClick={() => onBoxes(boxes.filter((_, item) => item !== index))}
              className="rounded border border-emerald-900 bg-emerald-950/40 px-2 py-1 text-emerald-300 disabled:cursor-default"
              title={disabled ? "Doğrulanmış kutu" : "Kutuyu sil"}
            >
              {box.category_name} · {Math.round(box.width)}×{Math.round(box.height)}
              {!disabled && " ×"}
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

export default function TrainingReviewPanel({
  eventId,
  onClose,
}: {
  eventId: string;
  onClose: () => void;
}) {
  const [canonicalEvent, setCanonicalEvent] = useState<CanonicalEvent | null>(null);
  const [reviews, setReviews] = useState<HumanReview[]>([]);
  const [incidentMedia, setIncidentMedia] = useState<IncidentMedia | null>(null);
  const [approvals, setApprovals] = useState<DevelopmentApproval[]>([]);
  const [learningPlan, setLearningPlan] = useState<LearningPlan | null>(null);
  const [selectedUses, setSelectedUses] = useState<DevelopmentUse[]>([]);
  const [samples, setSamples] = useState<TrainingSample[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [boxes, setBoxes] = useState<VerifiedBoundingBox[]>([]);
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("dortgoz.reviewer") ?? "operator");
  const [manifest, setManifest] = useState("training_manifest.json");
  const [times, setTimes] = useState({ start: 0, peak: 0, end: 0 });
  const [reviewOpen, setReviewOpen] = useState(false);
  const [reviewVerdict, setReviewVerdict] = useState<"anomali" | "sorun_degil">("anomali");
  const [reviewEventType, setReviewEventType] = useState<CanonicalEventType>("unknown_anomaly");
  const [reviewRisk, setReviewRisk] = useState<Risk>("orta");
  const [falseAlarmReason, setFalseAlarmReason] = useState("");
  const [intervention, setIntervention] = useState<"" | "yes" | "no">("");
  const [reviewNote, setReviewNote] = useState("");
  const [approvalNote, setApprovalNote] = useState(
    "İnsan doğrulamalı olay, seçilen geliştirme kullanımları için onaylandı.",
  );
  const [revokeOpen, setRevokeOpen] = useState(false);
  const [revocationNote, setRevocationNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setError("");
    const [
      eventResult,
      reviewResult,
      approvalResult,
      sampleResult,
      mediaResult,
      learningResult,
    ] = await Promise.all([
      getCanonicalEvent(eventId),
      getEventReviews(eventId),
      getDevelopmentApprovals(eventId),
      getTrainingSamples(eventId),
      getIncidentMedia(eventId),
      getLearningPlan(eventId),
    ]);
    setCanonicalEvent(eventResult);
    setReviews(reviewResult);
    setApprovals(approvalResult);
    setSamples(sampleResult);
    setIncidentMedia(mediaResult);
    setLearningPlan(learningResult);
    const latest = reviewResult.at(-1) ?? null;
    const latestDevelopment = approvalResult.at(-1) ?? null;
    setSelectedUses(
      latestDevelopment?.status === "approved"
        && latestDevelopment.review_id === latest?.review_id
        ? latestDevelopment.approved_uses
        : learningResult.routes
          .filter((route) => route.recommended && route.use !== "camera_rule")
          .map((route) => route.use),
    );
    setReviewOpen(false);
    setReviewVerdict(latest?.decision === "reject" ? "sorun_degil" : "anomali");
    const eventType = latest?.event_type ?? eventResult.event_type;
    setReviewEventType(
      EVENT_TYPES.includes(eventType as CanonicalEventType)
        ? eventType as CanonicalEventType
        : "unknown_anomaly",
    );
    setReviewRisk(
      (["dusuk", "orta", "yuksek", "kritik"] as string[]).includes(latest?.risk_level ?? "")
        ? latest!.risk_level as Risk
        : "orta",
    );
    setFalseAlarmReason(latest?.false_alarm_reason ?? "");
    setIntervention(
      latest?.intervention_required === true
        ? "yes"
        : latest?.intervention_required === false
          ? "no"
          : "",
    );
    setReviewNote(latest?.note ?? "");
    setSelectedId((current) => {
      if (sampleResult.some((sample) => sample.sample_id === current)) return current;
      return sampleResult.find((sample) => sample.status === "pending_review")?.sample_id
        ?? sampleResult[0]?.sample_id
        ?? "";
    });
    const start = eventResult.start_time ?? 0;
    const end = eventResult.end_time ?? start;
    setTimes({
      start,
      peak: eventResult.peak_time ?? (start + end) / 2,
      end,
    });
  }, [eventId]);

  useEffect(() => {
    load().catch((reason) => setError(messageOf(reason)));
  }, [load]);

  useEffect(() => {
    localStorage.setItem("dortgoz.reviewer", reviewer);
  }, [reviewer]);

  const selected = useMemo(
    () => samples.find((sample) => sample.sample_id === selectedId) ?? null,
    [samples, selectedId],
  );
  useEffect(() => {
    setBoxes(selected?.frame_review?.boxes ?? []);
  }, [selected]);

  const latestReview = reviews.at(-1) ?? null;
  const latestApproval = approvals.at(-1) ?? null;
  const activeDevelopmentApproval = latestApproval?.status === "approved"
    && latestApproval.review_id === latestReview?.review_id
    ? latestApproval
    : null;
  const activeApproval = activeDevelopmentApproval?.approved_uses.includes("d_fine_training")
    ? activeDevelopmentApproval
    : null;
  const approvalNeedsRenewal = latestApproval?.status === "approved"
    && latestApproval.review_id !== latestReview?.review_id;
  const recommendedApprovalUses = (learningPlan?.routes ?? [])
    .filter((route) => route.recommended && route.use !== "camera_rule")
    .map((route) => route.use);

  const run = async (
    operation: () => Promise<unknown>,
    success: string,
  ): Promise<boolean> => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await operation();
      await load();
      setNotice(success);
      return true;
    } catch (reason) {
      setError(messageOf(reason));
      return false;
    } finally {
      setBusy(false);
    }
  };

  const reviewerName = reviewer.trim();
  const validTimes = times.start >= 0 && times.start <= times.peak && times.peak <= times.end;
  const canSaveReview = Boolean(reviewerName && reviewNote.trim() && intervention)
    && (reviewVerdict === "anomali"
      ? validTimes
      : Boolean(falseAlarmReason));

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      <div className="flex max-h-[94vh] w-full max-w-6xl flex-col overflow-hidden rounded-md border border-zinc-800 bg-zinc-950 shadow-2xl">
        <header className="flex shrink-0 items-center gap-3 border-b border-zinc-800 px-4 py-3">
          <div>
            <h2 className="text-sm font-semibold text-zinc-100">Olayı İncele</h2>
            <p className="mt-0.5 text-[10px] text-zinc-500">
              Görüntüyü kontrol edin. Kararınızı verin.
            </p>
          </div>
          <div className="ml-auto">
            <button onClick={onClose} className="btn btn-ghost">
              Kapat ×
            </button>
          </div>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-1 overflow-y-auto lg:grid-cols-[23rem_minmax(0,1fr)] lg:overflow-hidden">
          <aside className="border-b border-zinc-800 p-3 text-xs lg:overflow-y-auto lg:border-b-0 lg:border-r">
            <section className="mb-3 rounded-md bg-zinc-900 p-3">
              <h3 className="font-medium text-zinc-100">Olay sonucu</h3>
              {canonicalEvent && (
                <p className="mt-1 text-zinc-400">
                  {CANONICAL_TYPE_TR[canonicalEvent.event_type as CanonicalEventType] ?? canonicalEvent.event_type}
                  {latestReview?.risk_level ? ` · ${RISK_TR[latestReview.risk_level as Risk] ?? latestReview.risk_level} önem` : ""}
                </p>
              )}
              {latestReview ? (
                <div className="mt-3 flex items-center justify-between gap-3">
                  <span className="font-medium text-emerald-400">✓ İnceleme tamamlandı</span>
                  {!reviewOpen && (
                    <button
                      type="button"
                      onClick={() => setReviewOpen(true)}
                      className="text-zinc-400 underline decoration-zinc-700 underline-offset-2 hover:text-zinc-200"
                    >
                      Kararı değiştir
                    </button>
                  )}
                </div>
              ) : (
                <div className="mt-3 space-y-2">
                  <p className="font-medium text-zinc-200">Bu olay doğru mu?</p>
                  <div className="grid grid-cols-2 gap-2">
                    <button
                      type="button"
                      disabled={busy || !reviewerName}
                      onClick={() => run(
                        () => saveEventReview(eventId, {
                          decision: "confirm",
                          reviewer: reviewerName,
                          note: "Operatör sistem sonucunu doğru olarak onayladı.",
                        }),
                        "Olay doğru olarak onaylandı.",
                      )}
                      className="btn btn-primary h-9"
                    >
                      Evet, doğru
                    </button>
                    <button
                      type="button"
                      disabled={busy || !reviewerName}
                      onClick={() => run(
                        () => saveEventReview(eventId, {
                          decision: "reject",
                          reviewer: reviewerName,
                          note: "Operatör kaydı sorun değil olarak işaretledi.",
                          false_alarm_reason: "other",
                          intervention_required: false,
                        }),
                        "Kayıt sorun değil olarak işaretlendi.",
                      )}
                      className="btn btn-outline h-9"
                    >
                      Hayır, sorun yok
                    </button>
                  </div>
                  {!reviewOpen && (
                    <button
                      type="button"
                      onClick={() => setReviewOpen(true)}
                      className="w-full py-1 text-center text-zinc-500 underline decoration-zinc-800 underline-offset-2 hover:text-zinc-300"
                    >
                      Ayrıntılı karar gir
                    </button>
                  )}
                </div>
              )}
            </section>

            {latestReview && recommendedApprovalUses.length > 0 && (
              <section className={`mb-3 rounded-md p-3 ${
                activeDevelopmentApproval
                  ? "border border-emerald-900 bg-emerald-950/30"
                  : "border border-sky-900 bg-sky-950/20"
              }`}>
                <h3 className="text-sm font-semibold text-zinc-100">
                  {activeDevelopmentApproval
                    ? "Onay verildi"
                    : approvalNeedsRenewal
                      ? "Yeniden onay verilsin mi?"
                      : "Sistem bu olaydan öğrensin mi?"}
                </h3>
                <p className="mt-1.5 leading-relaxed text-zinc-400">
                  {activeDevelopmentApproval
                    ? "Bu kayıt geliştirme hazırlığına alındı."
                    : "Onay verirseniz bu kayıt, sistemi geliştirmek için hazırlanır."}
                </p>
                <p className="mt-1 text-[10px] leading-relaxed text-zinc-500">
                  Canlı sistem otomatik değişmez. Otomatik eğitim başlamaz.
                </p>
                {activeDevelopmentApproval ? (
                  <button type="button" onClick={onClose} className="btn btn-primary mt-3 h-9 w-full">
                    Tamam, kapat
                  </button>
                ) : (
                  <button
                    type="button"
                    disabled={busy || !reviewerName}
                    onClick={async () => {
                      const completed = await run(
                        () => approveEventForLearning(eventId, {
                          review_id: latestReview.review_id,
                          approved_uses: recommendedApprovalUses,
                          reviewer: reviewerName,
                          note: "Operatör bu olayın sistem geliştirme hazırlığında kullanılmasını onayladı.",
                          ...(latestApproval ? { supersedes_approval_id: latestApproval.approval_id } : {}),
                        }),
                        "Geliştirme onayı kaydedildi.",
                      );
                      if (completed) onClose();
                    }}
                    className="btn btn-accent mt-3 h-9 w-full text-sm"
                  >
                    Onay ver ve kapat
                  </button>
                )}
              </section>
            )}

            {reviewOpen && canonicalEvent && (
              <div className="mb-2.5 space-y-2 rounded-md border border-amber-900 bg-amber-950 p-2.5">
                <div className="flex items-center justify-between">
                  <p className="text-amber-200">
                    {latestReview ? "Yeni bir karar revizyonu ekleyin." : "Model sonucunu doğrulayın."}
                  </p>
                  <button
                    type="button"
                    onClick={() => setReviewOpen(false)}
                    className="text-zinc-500 transition-colors hover:text-zinc-200"
                  >
                    Vazgeç ×
                  </button>
                </div>
                <label className="block text-zinc-400">
                  Karar
                  <select
                    value={reviewVerdict}
                    onChange={(event) => setReviewVerdict(event.target.value as "anomali" | "sorun_degil")}
                    className="field mt-0.5 w-full"
                  >
                    <option value="anomali">Anomali</option>
                    <option value="sorun_degil">Sorun değil</option>
                  </select>
                </label>
                {reviewVerdict === "anomali" ? (
                  <>
                    <label className="block text-zinc-400">
                      Doğru olay türü
                      <select
                        value={reviewEventType}
                        onChange={(event) => setReviewEventType(event.target.value as CanonicalEventType)}
                        className="field mt-0.5 w-full"
                      >
                        {EVENT_TYPES.map((eventType) => (
                          <option key={eventType} value={eventType}>{CANONICAL_TYPE_TR[eventType]}</option>
                        ))}
                      </select>
                    </label>
                    <label className="block text-zinc-400">
                      Doğru risk seviyesi
                      <select
                        value={reviewRisk}
                        onChange={(event) => setReviewRisk(event.target.value as Risk)}
                        className="field mt-0.5 w-full"
                      >
                        {(Object.entries(RISK_TR) as [Risk, string][]).map(([value, label]) => (
                          <option key={value} value={value}>{label}</option>
                        ))}
                      </select>
                    </label>
                    <div>
                      <div className="mb-0.5 text-zinc-400">Olay zamanı (saniye)</div>
                      <div className="grid grid-cols-3 gap-1">
                        {(["start", "peak", "end"] as const).map((field) => (
                          <label key={field} className="text-[10px] text-zinc-500">
                            {field === "start" ? "Başlangıç" : field === "peak" ? "Zirve" : "Bitiş"}
                            <input
                              type="number"
                              min="0"
                              step="0.1"
                              value={times[field]}
                              onChange={(event) => setTimes({ ...times, [field]: Number(event.target.value) })}
                              className="field mt-0.5 w-full text-right"
                            />
                          </label>
                        ))}
                      </div>
                      {!validTimes && <p className="mt-1 text-red-300">Başlangıç ≤ zirve ≤ bitiş olmalı.</p>}
                    </div>
                  </>
                ) : (
                  <label className="block text-zinc-400">
                    Yanlış alarm nedeni
                    <select
                      value={falseAlarmReason}
                      onChange={(event) => setFalseAlarmReason(event.target.value)}
                      className="field mt-0.5 w-full"
                    >
                      <option value="">Neden seçin</option>
                      {Object.entries(FALSE_ALARM_TR).map(([value, label]) => (
                        <option key={value} value={value}>{label}</option>
                      ))}
                    </select>
                  </label>
                )}
                <label className="block text-zinc-400">
                  Müdahale gerekli miydi?
                  <select
                    value={intervention}
                    onChange={(event) => setIntervention(event.target.value as "" | "yes" | "no")}
                    className="field mt-0.5 w-full"
                  >
                    <option value="">Seçin</option>
                    <option value="yes">Evet, gerekliydi</option>
                    <option value="no">Hayır, gerekli değildi</option>
                  </select>
                </label>
                <label className="block text-zinc-400">
                  Karar notu
                  <textarea
                    value={reviewNote}
                    onChange={(event) => setReviewNote(event.target.value)}
                    rows={2}
                    maxLength={4000}
                    className="field-area mt-0.5 resize-none"
                    placeholder="Düzeltmenin kısa gerekçesi"
                  />
                </label>
                <button
                  disabled={busy || !canSaveReview}
                  onClick={() => run(
                    () => saveEventReview(eventId, {
                      decision: reviewVerdict === "anomali" ? "edit" : "reject",
                      reviewer: reviewerName,
                      note: reviewNote.trim(),
                      ...(reviewVerdict === "anomali" ? {
                        event_type: reviewEventType,
                        risk_level: reviewRisk,
                        start_time: times.start,
                        peak_time: times.peak,
                        end_time: times.end,
                      } : {
                        false_alarm_reason: falseAlarmReason,
                      }),
                      intervention_required: intervention === "yes",
                    }),
                    latestReview ? "Olay kararı yeni revizyon olarak kaydedildi." : "Olay incelemesi kaydedildi.",
                  )}
                  className="btn btn-accent w-full"
                >
                  {latestReview ? "Düzeltmeyi yeni revizyon olarak kaydet" : "İnsan kararını kaydet"}
                </button>
                {latestReview && (
                  <p className="text-[10px] leading-relaxed text-amber-300">
                    İnceleme sonucu değişirse önceki geliştirme onayı geçersiz olur. Yeniden onay gerekir.
                  </p>
                )}
              </div>
            )}

            <details className="rounded-md border border-zinc-800 bg-zinc-950 p-2.5">
              <summary className="cursor-pointer select-none font-medium text-zinc-400 hover:text-zinc-200">
                Diğer bilgiler
              </summary>
              <div className="mt-3 space-y-2.5">
                <p className="text-[10px] leading-relaxed text-zinc-600">
                  Geçmiş kararlar ve geliştirme ekibi araçları bu alandadır.
                </p>
                <label className="block text-zinc-500">
                  İşlemi yapan
                  <input
                    value={reviewer}
                    onChange={(event) => setReviewer(event.target.value)}
                    className="field mt-1 w-full"
                  />
                </label>
                <p className="break-all font-mono text-[9px] text-zinc-700">Olay kimliği: {eventId}</p>

            {reviews.length > 0 && (
              <details className="mb-2.5 rounded-md border border-zinc-800 bg-zinc-950 p-2">
                <summary className="cursor-pointer text-zinc-400 hover:text-zinc-200">Karar geçmişi · {reviews.length}</summary>
                <div className="mt-2 space-y-2">
                  {[...reviews].reverse().map((review) => (
                    <div key={review.review_id} className="rounded-md border border-zinc-800 bg-zinc-900 p-2">
                      <div className="flex justify-between text-zinc-300">
                        <span>Revizyon {review.revision} · {REVIEW_DECISION_TR[review.decision]}</span>
                        <span className="text-zinc-600">{dateTime(review.created_at)}</span>
                      </div>
                      <div className="mt-1 text-zinc-500">
                        {review.decision === "reject"
                          ? FALSE_ALARM_TR[review.false_alarm_reason ?? ""] ?? review.false_alarm_reason
                          : CANONICAL_TYPE_TR[review.event_type as CanonicalEventType] ?? review.event_type}
                        {review.risk_level ? ` · ${RISK_TR[review.risk_level as Risk] ?? review.risk_level}` : ""}
                        {review.intervention_required !== null
                          ? ` · Müdahale ${review.intervention_required ? "gerekli" : "gerekli değil"}`
                          : ""}
                      </div>
                      <p className="mt-1 text-zinc-400">{review.note}</p>
                      <p className="mt-1 text-[10px] text-zinc-600">{review.reviewer}</p>
                    </div>
                  ))}
                </div>
              </details>
            )}

            {learningPlan && (
              <div className="mb-2.5 space-y-2 rounded-md border border-sky-900 bg-zinc-900 p-2.5">
                <div className="font-medium text-sky-300">Bu olay için geliştirme önerileri</div>
                {learningPlan.routes.some((route) => route.recommended) ? (
                  <div className="space-y-1.5">
                    {learningPlan.routes.filter((route) => route.recommended).map((route) => {
                      const presentation = presentationForUse(route.use);
                      return (
                        <div key={route.use} className="rounded-md bg-zinc-950 px-2 py-2">
                          <div className="flex items-start justify-between gap-2">
                            <span className="font-medium text-zinc-200">{presentation.title}</span>
                            <span className={route.ready ? "text-emerald-400" : "text-amber-400"}>
                              {operatorApprovalState(route)}
                            </span>
                          </div>
                          <p className="mt-1 text-[10px] leading-relaxed text-zinc-500">
                            {presentation.description}
                          </p>
                          <p className="mt-1 text-[10px] leading-relaxed text-sky-200">
                            <span className="font-medium">Önerilen işlem:</span>{" "}
                            {presentation.action}
                          </p>
                        </div>
                      );
                    })}
                  </div>
                ) : (
                  <p className="text-zinc-500">İnsan incelemesi tamamlandıktan sonra öneriler burada görünecek.</p>
                )}
                <details className="rounded-md border border-zinc-800 bg-zinc-950 p-2">
                  <summary className="cursor-pointer text-zinc-500 hover:text-zinc-300">
                    Teknik detaylar
                  </summary>
                  <div className="mt-2 space-y-2 text-[10px] text-zinc-500">
                    <p>
                      İç plan {learningPlan.plan_version} · skor {learningPlan.learning_score}/100 ·
                      müdahale {learningPlan.intervention_score ?? "—"}
                    </p>
                    <p>
                      Belirsizlik {learningPlan.components.uncertainty} · uyuşmazlık {learningPlan.components.disagreement} ·
                      yenilik {learningPlan.components.novelty} · kapsama açığı {learningPlan.components.coverage_gap}
                    </p>
                    <p>Davranış değişimi durumu: {systemBehaviorLabel(learningPlan.drift_state)}</p>
                    {learningPlan.routes.filter((route) => route.recommended).map((route) => {
                      const presentation = presentationForUse(route.use);
                      return (
                        <div key={route.use} className="border-t border-zinc-800 pt-2">
                          <p className="font-mono text-zinc-400">{route.use}</p>
                          <p>Kaynak bileşen: {presentation.technicalComponent}</p>
                          <p>Geliştirme türü: {presentation.technicalType}</p>
                          <p>İç durum: {route.approval_state}</p>
                          <p>{route.downstream} · {route.safety_gate}</p>
                        </div>
                      );
                    })}
                  </div>
                </details>
                <p className="text-[10px] text-zinc-500">
                  Otomatik eğitim ve canlı sisteme otomatik geçiş kapalıdır. Tüm geliştirmeler insan onayıyla ilerler.
                </p>
              </div>
            )}

            <div className="mb-2.5 space-y-1 rounded-md border border-zinc-800 bg-zinc-900 p-2.5">
              <div className="flex items-center gap-1.5 font-medium text-zinc-200">
                <span className="chip border border-zinc-700 text-zinc-300 font-mono">2</span>
                Geliştirme onayı
              </div>
              <div className={activeDevelopmentApproval ? "text-emerald-400" : latestApproval?.status === "revoked" ? "text-red-400" : approvalNeedsRenewal ? "text-amber-400" : "text-zinc-500"}>
                {activeDevelopmentApproval
                  ? `${activeDevelopmentApproval.approved_uses.length} işlem onaylandı`
                  : latestApproval?.status === "revoked"
                    ? "Geliştirme onayı geri alındı"
                    : approvalNeedsRenewal
                      ? "İnceleme sonucu değiştiği için yeniden onay gerekiyor."
                      : "Onay bekliyor"}
              </div>
              {latestReview && !activeDevelopmentApproval && (
                <div className="mt-2 space-y-2">
                  <div className="space-y-1 rounded-md border border-zinc-800 bg-zinc-950 p-1.5">
                    {(learningPlan?.routes ?? [])
                      .filter((route) => route.recommended && route.use !== "camera_rule")
                      .map((route) => (
                        <label key={route.use} className="flex items-start gap-2 text-zinc-300">
                          <input
                            type="checkbox"
                            checked={selectedUses.includes(route.use)}
                            onChange={(event) => setSelectedUses((current) =>
                              event.target.checked
                                ? [...new Set([...current, route.use])]
                                : current.filter((item) => item !== route.use))}
                            className="mt-0.5"
                          />
                          <span>
                            <span className="block">{presentationForUse(route.use).title}</span>
                            <span className="block text-[9px] text-zinc-600">
                              {presentationForUse(route.use).action}
                            </span>
                          </span>
                        </label>
                      ))}
                  </div>
                  <textarea
                    value={approvalNote}
                    onChange={(event) => setApprovalNote(event.target.value)}
                    rows={2}
                    maxLength={4000}
                    className="field-area"
                    aria-label="Geliştirme kullanım onay notu"
                  />
                  <button
                    disabled={busy || !reviewerName || !approvalNote.trim() || selectedUses.length === 0}
                    onClick={() => run(
                      () => approveEventForLearning(eventId, {
                        review_id: latestReview.review_id,
                        approved_uses: selectedUses,
                        reviewer: reviewerName,
                        note: approvalNote.trim(),
                        ...(latestApproval ? { supersedes_approval_id: latestApproval.approval_id } : {}),
                      }),
                      "Seçilen geliştirme işlemleri onaylandı.",
                    )}
                    className="btn btn-accent w-full"
                  >
                    Seçilen işlemleri onayla
                  </button>
                </div>
              )}
              {activeDevelopmentApproval && !revokeOpen && (
                <button
                  type="button"
                  onClick={() => setRevokeOpen(true)}
                  className="btn btn-outline mt-2 w-full border-red-900 text-red-300 hover:border-red-700 hover:text-red-200"
                >
                  Geliştirme onayını geri al
                </button>
              )}
              {activeDevelopmentApproval && revokeOpen && (
                <div className="mt-2 space-y-2 rounded-md border border-red-900 bg-red-950 p-2.5">
                  <p className="text-red-200">Bu onaya bağlı bütün hazır işlemler durdurulacak.</p>
                  <textarea
                    value={revocationNote}
                    onChange={(event) => setRevocationNote(event.target.value)}
                    rows={2}
                    maxLength={4000}
                    className="field-area"
                    placeholder="Geri alma gerekçesi"
                    aria-label="Geliştirme onayı geri alma gerekçesi"
                  />
                  <div className="grid grid-cols-2 gap-1.5">
                    <button
                      type="button"
                      onClick={() => {
                        setRevokeOpen(false);
                        setRevocationNote("");
                      }}
                      className="btn btn-outline"
                    >
                      Vazgeç
                    </button>
                    <button
                      disabled={busy || !reviewerName || !revocationNote.trim()}
                      onClick={() => run(
                        async () => {
                          const result = await revokeEventLearningApproval(eventId, {
                            review_id: activeDevelopmentApproval.review_id,
                            reviewer: reviewerName,
                            note: revocationNote.trim(),
                            supersedes_approval_id: activeDevelopmentApproval.approval_id,
                          });
                          setRevokeOpen(false);
                          setRevocationNote("");
                          return result;
                        },
                        "Geliştirme onayı geri alındı.",
                      )}
                      className="btn btn-danger"
                    >
                      Onayı geri al
                    </button>
                  </div>
                </div>
              )}
            </div>

            {approvals.length > 0 && (
              <details className="mb-2.5 rounded-md border border-zinc-800 bg-zinc-950 p-2">
                <summary className="cursor-pointer text-zinc-400 hover:text-zinc-200">Onay geçmişi · {approvals.length}</summary>
                <div className="mt-2 space-y-2">
                  {[...approvals].reverse().map((approval) => (
                    <div key={approval.approval_id} className="rounded-md border border-zinc-800 bg-zinc-900 p-2">
                      <div className="flex justify-between text-zinc-300">
                        <span>{APPROVAL_STATUS_TR[approval.status]}</span>
                        <span className="text-zinc-600">{dateTime(approval.created_at)}</span>
                      </div>
                      <p className="mt-1 text-zinc-400">{approval.note}</p>
                      <p className="mt-1 text-[10px] text-zinc-600">{approval.reviewer}</p>
                    </div>
                  ))}
                </div>
              </details>
            )}

            <div className="mb-2.5 space-y-2 rounded-md border border-zinc-800 bg-zinc-900 p-2.5">
              <div className="flex items-center gap-1.5 font-medium text-zinc-200">
                <span className="chip border border-zinc-700 text-zinc-300 font-mono">3</span>
                Kareleri hazırla
              </div>
              <input
                value={manifest}
                onChange={(event) => setManifest(event.target.value)}
                className="field w-full font-mono"
                placeholder="training_manifest.json"
              />
              <button
                disabled={busy || !activeApproval || !manifest.trim() || !reviewerName}
                onClick={() => run(
                  () => prepareTrainingSamples(eventId, {
                    approval_id: activeApproval!.approval_id,
                    dataset_manifest_name: manifest.trim(),
                    prepared_by: reviewerName,
                  }),
                  "Olay kareleri hazırlandı.",
                )}
                className="btn btn-accent w-full"
              >
                Başlangıç · zirve · bitiş karelerini çıkar
              </button>
            </div>

            <div className="rounded-md border border-zinc-800 bg-zinc-900 p-2.5">
              <div className="mb-1.5 flex items-center gap-1.5 font-medium text-zinc-200">
                <span className="chip border border-zinc-700 text-zinc-300 font-mono">4</span>
                Kare doğrulama
              </div>
              <div className="space-y-1">
                {samples.length === 0 && <p className="text-zinc-600">Hazırlanmış kare yok.</p>}
                {samples.map((sample) => (
                  <button
                    key={sample.sample_id}
                    onClick={() => setSelectedId(sample.sample_id)}
                    className={`flex w-full items-center gap-2 rounded-md border p-1.5 text-left transition-colors ${
                      selectedId === sample.sample_id
                        ? "border-sky-800 bg-sky-950/30"
                        : "border-zinc-800 bg-zinc-950 hover:border-zinc-600"
                    }`}
                  >
                    <img src={sample.frame_url} alt="" className="h-10 w-16 rounded-sm bg-black object-cover" />
                    <span className="min-w-0">
                      <span className="block text-zinc-300">
                        {REASON_TR[sample.selection_reason] ?? sample.selection_reason} · {clock(sample.timestamp_seconds)}
                      </span>
                      <span className={sample.status === "verified" ? "text-emerald-400" : sample.status === "revoked" ? "text-red-400" : "text-amber-400"}>
                        {STATUS_TR[sample.status]}
                      </span>
                    </span>
                  </button>
                ))}
              </div>
            </div>
              </div>
            </details>
          </aside>

          <main className="min-h-0 overflow-y-auto p-4">
            {error && (
              <div className="mb-3 rounded-md border border-red-900 bg-red-950 px-3 py-2 text-sm text-red-300">
                {error}
              </div>
            )}
            {notice && (
              <div className="mb-3 rounded-md border border-emerald-900 bg-emerald-950 px-3 py-2 text-sm text-emerald-300">
                {notice}
              </div>
            )}
            {incidentMedia && (
              <section className="mx-auto mb-4 max-w-4xl rounded-md border border-zinc-800 bg-zinc-900 p-2.5">
                <div className="mb-2 flex items-center text-xs text-zinc-400">
                  <span>Olay klibi</span>
                  <span className="ml-auto font-mono">
                    {clock(incidentMedia.clip_start)}–{clock(incidentMedia.clip_end)}
                  </span>
                </div>
                <video
                  controls
                  preload="metadata"
                  poster={incidentMedia.thumbnail_url}
                  src={incidentMedia.clip_url}
                  className="max-h-72 w-full rounded-md bg-black object-contain"
                >
                  Tarayıcınız olay klibini oynatamıyor.
                </video>
              </section>
            )}
            {selected && (
              <div className="mx-auto max-w-4xl space-y-3">
                <div className="flex items-center gap-2 text-xs text-zinc-400">
                  <span className="chip bg-zinc-800 text-zinc-300">
                    {REASON_TR[selected.selection_reason] ?? selected.selection_reason}
                  </span>
                  <span className="font-mono">{clock(selected.timestamp_seconds)}</span>
                  <span>{selected.image_width}×{selected.image_height}</span>
                  <span>{selected.split}</span>
                  <span className="ml-auto">{STATUS_TR[selected.status]}</span>
                </div>
                <BoxEditor sample={selected} boxes={boxes} onBoxes={setBoxes} />
                {selected.status === "pending_review" && (
                  <div className="flex justify-end gap-2 border-t border-zinc-800 pt-3">
                    <button
                      disabled={busy || !reviewerName}
                      onClick={() => run(
                        () => verifyTrainingSample(selected.sample_id, {
                          review_result: "verified_no_target_objects",
                          boxes: [],
                          reviewer: reviewerName,
                        }),
                        "Kare hedef nesne içermiyor olarak doğrulandı.",
                      )}
                      className="btn btn-outline"
                    >
                      Hedef nesne yok
                    </button>
                    <button
                      disabled={busy || !reviewerName || boxes.length === 0}
                      onClick={() => run(
                        () => verifyTrainingSample(selected.sample_id, {
                          review_result: "verified_boxes",
                          boxes,
                          reviewer: reviewerName,
                        }),
                        `${boxes.length} kutu doğrulandı.`,
                      )}
                      className="btn btn-primary"
                    >
                      {boxes.length} kutuyu doğrula
                    </button>
                  </div>
                )}
                {selected.status === "verified" && selected.frame_review && (
                  <div className="rounded-md border border-emerald-900 bg-emerald-950 px-3 py-2 text-sm text-emerald-300">
                    İnsan doğrulaması tamamlandı · {selected.frame_review.reviewer} · {selected.frame_review.boxes.length} kutu
                  </div>
                )}
                {selected.status === "revoked" && (
                  <div className="rounded-md border border-red-900 bg-red-950 px-3 py-2 text-sm text-red-300">
                    Bu örnek daha yeni bir inceleme veya izin kararı nedeniyle geçersizdir. Eğitime aktarılmaz.
                  </div>
                )}
              </div>
            )}
          </main>
        </div>
      </div>
    </div>
  );
}

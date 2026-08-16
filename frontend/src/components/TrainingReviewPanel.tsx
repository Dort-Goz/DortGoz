import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type PointerEvent as ReactPointerEvent,
} from "react";
import {
  ApiError,
  approveEventForDFine,
  getCanonicalEvent,
  getDevelopmentApprovals,
  getEventReviews,
  getIncidentMedia,
  getTrainingSamples,
  prepareTrainingSamples,
  saveEventReview,
  verifyTrainingSample,
} from "../lib/api";
import { boxFromPoints, imagePoint, type ImagePoint } from "../lib/trainingBoxes";
import type {
  CanonicalEvent,
  DevelopmentApproval,
  HumanReview,
  IncidentMedia,
  TrainingSample,
  VerifiedBoundingBox,
} from "../types/domain";

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
      <div className="relative overflow-hidden rounded border border-zinc-700 bg-black">
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
            className="w-36 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 font-mono"
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
  const [samples, setSamples] = useState<TrainingSample[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [boxes, setBoxes] = useState<VerifiedBoundingBox[]>([]);
  const [reviewer, setReviewer] = useState(() => localStorage.getItem("dortgoz.reviewer") ?? "operator");
  const [manifest, setManifest] = useState("training_manifest.json");
  const [times, setTimes] = useState({ start: 0, peak: 0, end: 0 });
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const load = useCallback(async () => {
    setError("");
    const [eventResult, reviewResult, approvalResult, sampleResult, mediaResult] = await Promise.all([
      getCanonicalEvent(eventId),
      getEventReviews(eventId),
      getDevelopmentApprovals(eventId),
      getTrainingSamples(eventId),
      getIncidentMedia(eventId),
    ]);
    setCanonicalEvent(eventResult);
    setReviews(reviewResult);
    setApprovals(approvalResult);
    setSamples(sampleResult);
    setIncidentMedia(mediaResult);
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
  const activeApproval = latestApproval?.status === "approved"
    && latestApproval.review_id === latestReview?.review_id
    && latestApproval.approved_uses.includes("d_fine_training")
    ? latestApproval
    : null;

  const run = async (operation: () => Promise<unknown>, success: string) => {
    setBusy(true);
    setError("");
    setNotice("");
    try {
      await operation();
      await load();
      setNotice(success);
    } catch (reason) {
      setError(messageOf(reason));
    } finally {
      setBusy(false);
    }
  };

  const reviewerName = reviewer.trim();
  const validTimes = times.start >= 0 && times.start <= times.peak && times.peak <= times.end;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/75 p-4">
      <div className="flex max-h-[94vh] w-full max-w-6xl flex-col overflow-hidden rounded-xl border border-zinc-700 bg-zinc-950 shadow-2xl">
        <header className="flex items-center gap-3 border-b border-zinc-800 px-4 py-3">
          <div>
            <h2 className="font-semibold text-zinc-100">Kontrollü eğitim örneği</h2>
            <p className="font-mono text-[10px] text-zinc-500">{eventId}</p>
          </div>
          <div className="ml-auto flex items-center gap-2 text-xs">
            <label className="text-zinc-400">İnceleyen</label>
            <input
              value={reviewer}
              onChange={(event) => setReviewer(event.target.value)}
              className="w-36 rounded border border-zinc-700 bg-zinc-900 px-2 py-1"
            />
            <button onClick={onClose} className="rounded px-2 py-1 text-zinc-400 hover:bg-zinc-800">
              Kapat ×
            </button>
          </div>
        </header>

        <div className="grid min-h-0 flex-1 grid-cols-[19rem_minmax(0,1fr)]">
          <aside className="overflow-y-auto border-r border-zinc-800 p-3 text-xs">
            <div className="mb-3 space-y-1 rounded border border-zinc-800 bg-zinc-900/60 p-2">
              <div className="font-medium text-zinc-200">1 · Olay incelemesi</div>
              <div className={latestReview ? "text-emerald-400" : "text-amber-400"}>
                {latestReview ? `Hazır · revizyon ${latestReview.revision}` : "İnsan kararı gerekli"}
              </div>
              {canonicalEvent && (
                <div className="text-zinc-500">{canonicalEvent.event_type} · {canonicalEvent.status}</div>
              )}
            </div>

            {!latestReview && canonicalEvent && (
              <div className="mb-3 space-y-2 rounded border border-amber-900/60 bg-amber-950/20 p-2">
                <p className="text-amber-200">Model zamanlarını doğrulayın.</p>
                {(["start", "peak", "end"] as const).map((field) => (
                  <label key={field} className="flex items-center justify-between gap-2">
                    <span className="text-zinc-400">
                      {field === "start" ? "Başlangıç" : field === "peak" ? "Zirve" : "Bitiş"}
                    </span>
                    <input
                      type="number"
                      min="0"
                      step="0.1"
                      value={times[field]}
                      onChange={(event) => setTimes({ ...times, [field]: Number(event.target.value) })}
                      className="w-24 rounded border border-zinc-700 bg-zinc-900 px-2 py-1 text-right"
                    />
                  </label>
                ))}
                <button
                  disabled={busy || !reviewerName || !validTimes}
                  onClick={() => run(
                    () => saveEventReview(eventId, {
                      decision: "edit",
                      reviewer: reviewerName,
                      note: "Olay ve zaman aralığı operatör tarafından doğrulandı.",
                      start_time: times.start,
                      peak_time: times.peak,
                      end_time: times.end,
                    }),
                    "Olay incelemesi kaydedildi.",
                  )}
                  className="w-full rounded bg-amber-700 px-2 py-1.5 font-medium text-white disabled:opacity-40"
                >
                  Zamanları doğrula
                </button>
              </div>
            )}

            <div className="mb-3 space-y-1 rounded border border-zinc-800 bg-zinc-900/60 p-2">
              <div className="font-medium text-zinc-200">2 · Geliştirme izni</div>
              <div className={activeApproval ? "text-emerald-400" : "text-zinc-500"}>
                {activeApproval ? "D-FINE kullanımı onaylı" : "Henüz izin verilmedi"}
              </div>
              {latestReview && !activeApproval && (
                <button
                  disabled={busy || !reviewerName}
                  onClick={() => run(
                    () => approveEventForDFine(eventId, {
                      review_id: latestReview.review_id,
                      reviewer: reviewerName,
                      note: "İnsan doğrulamalı kareler D-FINE eğitimi için onaylandı.",
                      ...(latestApproval ? { supersedes_approval_id: latestApproval.approval_id } : {}),
                    }),
                    "D-FINE kullanım izni kaydedildi.",
                  )}
                  className="mt-2 w-full rounded bg-indigo-700 px-2 py-1.5 font-medium text-white disabled:opacity-40"
                >
                  D-FINE için onayla
                </button>
              )}
            </div>

            <div className="mb-3 space-y-2 rounded border border-zinc-800 bg-zinc-900/60 p-2">
              <div className="font-medium text-zinc-200">3 · Kareleri hazırla</div>
              <input
                value={manifest}
                onChange={(event) => setManifest(event.target.value)}
                className="w-full rounded border border-zinc-700 bg-zinc-950 px-2 py-1 font-mono"
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
                className="w-full rounded bg-sky-700 px-2 py-1.5 font-medium text-white disabled:opacity-40"
              >
                Başlangıç · zirve · bitiş karelerini çıkar
              </button>
            </div>

            <div className="space-y-1">
              <div className="font-medium text-zinc-300">4 · Kare doğrulama</div>
              {samples.length === 0 && <p className="text-zinc-600">Hazırlanmış kare yok.</p>}
              {samples.map((sample) => (
                <button
                  key={sample.sample_id}
                  onClick={() => setSelectedId(sample.sample_id)}
                  className={`flex w-full items-center gap-2 rounded border p-1.5 text-left ${
                    selectedId === sample.sample_id
                      ? "border-sky-700 bg-sky-950/30"
                      : "border-zinc-800 bg-zinc-900/40"
                  }`}
                >
                  <img src={sample.frame_url} alt="" className="h-10 w-16 rounded bg-black object-cover" />
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
          </aside>

          <main className="min-h-0 overflow-y-auto p-4">
            {error && (
              <div className="mb-3 rounded border border-red-900 bg-red-950/40 px-3 py-2 text-sm text-red-300">
                {error}
              </div>
            )}
            {notice && (
              <div className="mb-3 rounded border border-emerald-900 bg-emerald-950/40 px-3 py-2 text-sm text-emerald-300">
                {notice}
              </div>
            )}
            {incidentMedia && (
              <section className="mx-auto mb-4 max-w-4xl rounded-lg border border-zinc-800 bg-zinc-900/40 p-3">
                <div className="mb-2 flex items-center text-xs text-zinc-400">
                  <span>Olay klibi</span>
                  <span className="ml-auto">
                    {clock(incidentMedia.clip_start)}–{clock(incidentMedia.clip_end)}
                  </span>
                </div>
                <video
                  controls
                  preload="metadata"
                  poster={incidentMedia.thumbnail_url}
                  src={incidentMedia.clip_url}
                  className="max-h-72 w-full rounded bg-black object-contain"
                >
                  Tarayıcınız olay klibini oynatamıyor.
                </video>
              </section>
            )}
            {!selected && (
              <div className="flex h-full min-h-72 items-center justify-center text-sm text-zinc-600">
                Sol taraftaki adımları tamamlayın. Hazırlanan kare burada açılır.
              </div>
            )}
            {selected && (
              <div className="mx-auto max-w-4xl space-y-3">
                <div className="flex items-center gap-2 text-xs text-zinc-400">
                  <span className="rounded bg-zinc-800 px-2 py-1">
                    {REASON_TR[selected.selection_reason] ?? selected.selection_reason}
                  </span>
                  <span>{clock(selected.timestamp_seconds)}</span>
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
                      className="rounded border border-zinc-700 px-3 py-1.5 text-sm hover:bg-zinc-800 disabled:opacity-40"
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
                      className="rounded bg-emerald-700 px-3 py-1.5 text-sm font-medium text-white disabled:opacity-40"
                    >
                      {boxes.length} kutuyu doğrula
                    </button>
                  </div>
                )}
                {selected.status === "verified" && selected.frame_review && (
                  <div className="rounded border border-emerald-900 bg-emerald-950/30 px-3 py-2 text-sm text-emerald-300">
                    İnsan doğrulaması tamamlandı · {selected.frame_review.reviewer} · {selected.frame_review.boxes.length} kutu
                  </div>
                )}
                {selected.status === "revoked" && (
                  <div className="rounded border border-red-900 bg-red-950/30 px-3 py-2 text-sm text-red-300">
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

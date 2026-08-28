import { useEffect, useState } from "react";
import {
  approveEventForDFine,
  getDevelopmentApprovals,
  getEventReviews,
  getIncidentMedia,
  getMaintenanceReviews,
  rejectEventLearningApproval,
} from "../lib/api";
import { CANONICAL_TYPE_TR } from "../lib/labels";
import type {
  DevelopmentApproval,
  HumanReview,
  IncidentMedia,
  MaintenanceReview,
} from "../types/domain";
import type { CanonicalEventType } from "../types/events";

const DECISION_TR = {
  confirm: "Anomali doğru",
  edit: "Anomali düzeltildi",
  reject: "Anomali yok",
} as const;

function categoryLabel(value: string | null): string {
  if (!value) return "—";
  return CANONICAL_TYPE_TR[value as CanonicalEventType] ?? value;
}

function DecisionBox({
  title,
  decision,
  eventType,
  note,
}: {
  title: string;
  decision: "confirm" | "reject" | "edit";
  eventType: string | null;
  note: string;
}) {
  return (
    <section className="border border-zinc-800 bg-zinc-900 p-3">
      <div className="microlabel mb-2">{title}</div>
      <div className="flex flex-wrap gap-2">
        <span className="chip border border-zinc-700 bg-zinc-950 text-zinc-100">
          {DECISION_TR[decision]}
        </span>
        {decision !== "reject" && (
          <span className="chip border border-zinc-700 bg-zinc-950 text-zinc-300">
            {categoryLabel(eventType)}
          </span>
        )}
      </div>
      <p className="mt-2 whitespace-pre-wrap text-zinc-300">{note}</p>
    </section>
  );
}

export default function FineTuneDecisionDialog({
  eventId,
  user,
  onClose,
  onSaved,
}: {
  eventId: string;
  user: string;
  onClose: () => void;
  onSaved: (accepted: boolean) => void;
}) {
  const [operatorReview, setOperatorReview] = useState<HumanReview | null>(null);
  const [maintenanceReview, setMaintenanceReview] = useState<MaintenanceReview | null>(null);
  const [latestApproval, setLatestApproval] = useState<DevelopmentApproval | null>(null);
  const [media, setMedia] = useState<IncidentMedia | null>(null);
  const [busy, setBusy] = useState<"accept" | "reject" | "">("");
  const [error, setError] = useState("");

  useEffect(() => {
    let cancelled = false;
    void Promise.all([
      getEventReviews(eventId),
      getMaintenanceReviews(eventId),
      getDevelopmentApprovals(eventId),
      getIncidentMedia(eventId),
    ]).then(([operatorReviews, maintenanceReviews, approvals, incidentMedia]) => {
      if (cancelled) return;
      setOperatorReview(operatorReviews.at(-1) ?? null);
      setMaintenanceReview(maintenanceReviews.at(-1) ?? null);
      setLatestApproval(approvals.at(-1) ?? null);
      setMedia(incidentMedia);
    }).catch((reason) => {
      if (!cancelled) {
        setError(reason instanceof Error ? reason.message : "Fine-tune kararı açılamadı.");
      }
    });
    return () => {
      cancelled = true;
    };
  }, [eventId]);

  const decide = async (accepted: boolean) => {
    if (!operatorReview || !maintenanceReview || !user.trim()) return;
    setBusy(accepted ? "accept" : "reject");
    setError("");
    const common = {
      review_id: operatorReview.review_id,
      maintenance_review_id: maintenanceReview.maintenance_review_id,
      reviewer: user.trim(),
      note: accepted ? "D-FINE fine-tune kuyruğuna gönderildi." : "Fine-tune istenmedi.",
      ...(latestApproval
        ? { supersedes_approval_id: latestApproval.approval_id }
        : {}),
    };
    try {
      if (accepted) {
        await approveEventForDFine(eventId, common);
      } else {
        await rejectEventLearningApproval(eventId, common);
      }
      onSaved(accepted);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Fine-tune kararı kaydedilemedi.");
    } finally {
      setBusy("");
    }
  };

  const ready = Boolean(operatorReview && maintenanceReview && user.trim());

  return (
    <div className="fixed inset-0 z-[75] flex items-center justify-center bg-black/75 p-4">
      <section
        role="dialog"
        aria-modal="true"
        aria-label="Fine-tune kararı"
        className="w-full max-w-2xl overflow-hidden rounded-md border border-zinc-800 bg-zinc-950 shadow-2xl"
      >
        <header className="flex items-center border-b border-zinc-800 px-4 py-3">
          <h2 className="text-sm font-semibold text-zinc-100">Fine-tune Kararı</h2>
          <button type="button" onClick={onClose} className="btn btn-ghost ml-auto">
            Kapat ×
          </button>
        </header>

        <div className="space-y-3 p-4 text-xs">
          {error && (
            <div className="border border-red-900 bg-red-950/40 px-3 py-2 text-red-200">
              {error}
            </div>
          )}

          {media && (
            <img
              src={media.thumbnail_url}
              alt="Olay görüntüsü"
              className="max-h-52 w-full border border-zinc-800 bg-black object-contain"
            />
          )}

          {operatorReview && (
            <DecisionBox
              title="Operatör kararı"
              decision={operatorReview.decision}
              eventType={operatorReview.event_type}
              note={operatorReview.note}
            />
          )}
          {maintenanceReview && (
            <DecisionBox
              title="IT kararı"
              decision={maintenanceReview.decision}
              eventType={maintenanceReview.event_type}
              note={maintenanceReview.note}
            />
          )}

          <div className="grid grid-cols-1 gap-2 sm:grid-cols-2">
            <button
              type="button"
              disabled={!ready || busy !== ""}
              onClick={() => void decide(false)}
              className="btn btn-outline btn-wrap h-11"
            >
              {busy === "reject" ? "Kaydediliyor…" : "İstemiyorum"}
            </button>
            <button
              type="button"
              disabled={!ready || busy !== ""}
              onClick={() => void decide(true)}
              className="btn btn-accent btn-wrap h-11"
            >
              {busy === "accept" ? "Gönderiliyor…" : "Fine-tune'a gönder"}
            </button>
          </div>
        </div>
      </section>
    </div>
  );
}

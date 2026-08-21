import type {
  CanonicalEvent,
  DevelopmentApproval,
  DevelopmentUse,
  HumanReview,
  IncidentMedia,
  InterventionPriority,
  TrainingSample,
  LearningPlan,
  VerifiedBoundingBox,
  VideoMetadata,
} from "../types/domain";

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly code: string,
    public readonly status: number,
  ) {
    super(message);
  }
}

export async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new ApiError(
      body?.error?.message ?? body?.detail ?? `İstek başarısız (${response.status})`,
      body?.error?.code ?? "HTTP_ERROR",
      response.status,
    );
  }
  return response.json() as Promise<T>;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});

export function uploadVideo(file: File) {
  const body = new FormData();
  body.append("file", file);
  return request<VideoMetadata>("/api/videos", { method: "POST", body });
}

export const getCanonicalEvent = (eventId: string) =>
  request<CanonicalEvent>(`/api/events/${encodeURIComponent(eventId)}`);

export const getEventReviews = (eventId: string) =>
  request<HumanReview[]>(`/api/events/${encodeURIComponent(eventId)}/reviews`);

export async function getIncidentMedia(eventId: string): Promise<IncidentMedia | null> {
  try {
    return await request<IncidentMedia>(
      `/api/events/${encodeURIComponent(eventId)}/media`,
    );
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export async function getInterventionPriority(
  eventId: string,
): Promise<InterventionPriority | null> {
  try {
    return await request<InterventionPriority>(
      `/api/events/${encodeURIComponent(eventId)}/priority`,
    );
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return null;
    throw error;
  }
}

export interface HumanReviewInput {
  decision: "confirm" | "reject" | "edit";
  reviewer: string;
  note: string;
  event_type?: string;
  start_time?: number;
  peak_time?: number;
  end_time?: number;
  risk_level?: string;
  false_alarm_reason?: string;
  intervention_required?: boolean;
}

export const saveEventReview = (eventId: string, body: HumanReviewInput) =>
  request<HumanReview>(`/api/events/${encodeURIComponent(eventId)}/review`, json(body));

export const getDevelopmentApprovals = (eventId: string) =>
  request<DevelopmentApproval[]>(
    `/api/events/${encodeURIComponent(eventId)}/development-approvals`,
  );

export const getLearningPlan = (eventId: string) =>
  request<LearningPlan>(`/api/events/${encodeURIComponent(eventId)}/learning-plan`);

export const approveEventForLearning = (
  eventId: string,
  body: {
    review_id: string;
    approved_uses: DevelopmentUse[];
    reviewer: string;
    note: string;
    supersedes_approval_id?: string;
  },
) => request<DevelopmentApproval>(
  `/api/events/${encodeURIComponent(eventId)}/development-approval`,
  json({ ...body, status: "approved" }),
);

export const approveEventForDFine = (
  eventId: string,
  body: {
    review_id: string;
    reviewer: string;
    note: string;
    supersedes_approval_id?: string;
  },
) => approveEventForLearning(eventId, {
  ...body,
  approved_uses: ["d_fine_training"],
});

export const revokeEventLearningApproval = (
  eventId: string,
  body: {
    review_id: string;
    reviewer: string;
    note: string;
    supersedes_approval_id: string;
  },
) => request<DevelopmentApproval>(
  `/api/events/${encodeURIComponent(eventId)}/development-approval`,
  json({ ...body, status: "revoked", approved_uses: [] }),
);

export const revokeEventDFineApproval = revokeEventLearningApproval;

export const getTrainingSamples = (eventId: string) =>
  request<TrainingSample[]>(`/api/events/${encodeURIComponent(eventId)}/training-samples`);

export const prepareTrainingSamples = (
  eventId: string,
  body: { approval_id: string; dataset_manifest_name: string; prepared_by: string },
) => request<TrainingSample[]>(
  `/api/events/${encodeURIComponent(eventId)}/training-samples`,
  json(body),
);

export const verifyTrainingSample = (
  sampleId: string,
  body: {
    review_result: "verified_boxes" | "verified_no_target_objects";
    boxes: VerifiedBoundingBox[];
    reviewer: string;
  },
) => request<TrainingSample>(
  `/api/training-samples/${encodeURIComponent(sampleId)}/review`,
  json({ ...body, annotation_tool: "Dortgoz UI" }),
);

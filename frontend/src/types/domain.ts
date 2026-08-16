export interface VideoMetadata {
  video_id: string;
  stored_filename: string;
  media_path: string;
  original_filename: string;
  duration_seconds: number;
}

export interface CanonicalEvent {
  event_id: string;
  analysis_id: string;
  video_id: string;
  status: "confirmed" | "rejected" | "human_review" | "processing_failed";
  event_type: string;
  start_time: number | null;
  peak_time: number | null;
  end_time: number | null;
  confidence: number | null;
  revision: number;
}

export interface HumanReview {
  review_id: string;
  event_id: string;
  decision: "confirm" | "reject" | "edit";
  start_time: number | null;
  peak_time: number | null;
  end_time: number | null;
  note: string;
  reviewer: string;
  revision: number;
  created_at: string;
}

export interface IncidentMedia {
  media_id: string;
  event_id: string;
  event_revision: number;
  clip_url: string;
  thumbnail_url: string;
  clip_start: number;
  clip_end: number;
  peak_time: number;
  clip_sha256: string;
  revision: number;
}

export interface InterventionPriority {
  priority_id: string;
  event_id: string;
  analysis_id: string;
  event_revision: number;
  score: number;
  band: "routine" | "review" | "high" | "urgent";
  reasons: string[];
  risk_input: "dusuk" | "orta" | "yuksek" | "kritik";
  event_type_input: string;
  phase_input: string;
  needs_review_input: boolean;
  model_confidence: number | null;
  ruleset_version: string;
  revision: number;
  created_at: string;
  calculated_at: string;
}

export interface DevelopmentApproval {
  approval_id: string;
  event_id: string;
  review_id: string;
  status: "approved" | "rejected" | "revoked";
  approved_uses: string[];
  reviewer: string;
  note: string;
  supersedes_approval_id: string | null;
  created_at: string;
}

export interface VerifiedBoundingBox {
  category_name: string;
  x: number;
  y: number;
  width: number;
  height: number;
  is_crowd?: boolean;
}

export interface TrainingFrameReview {
  review_result: "verified_boxes" | "verified_no_target_objects";
  boxes: VerifiedBoundingBox[];
  reviewer: string;
  reviewed_at: string;
}

export interface TrainingSample {
  sample_id: string;
  event_id: string;
  timestamp_seconds: number;
  selection_reason: string;
  frame_url: string;
  image_width: number;
  image_height: number;
  split: "train" | "validation";
  status: "pending_review" | "verified" | "revoked";
  frame_review: TrainingFrameReview | null;
  revoked_by_approval_id: string | null;
  invalidated_by_review_id: string | null;
}

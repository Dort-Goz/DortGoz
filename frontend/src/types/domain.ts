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
  event_type: string | null;
  start_time: number | null;
  peak_time: number | null;
  end_time: number | null;
  risk_level: string | null;
  false_alarm_reason: string | null;
  intervention_required: boolean | null;
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

export type DevelopmentUse =
  | "camera_rule"
  | "prompt_example"
  | "threshold_calibration"
  | "siglip_training"
  | "d_fine_training"
  | "evaluation";

export interface DevelopmentApproval {
  approval_id: string;
  event_id: string;
  review_id: string;
  status: "approved" | "rejected" | "revoked";
  approved_uses: DevelopmentUse[];
  reviewer: string;
  note: string;
  supersedes_approval_id: string | null;
  created_at: string;
}

export interface LearningRoute {
  use: DevelopmentUse;
  recommended: boolean;
  approval_state:
    | "review_required"
    | "approval_required"
    | "approved"
    | "not_approved"
    | "rejected"
    | "revoked"
    | "stale";
  ready: boolean;
  downstream: string;
  reason: string;
  safety_gate: string;
}

export interface LearningPlan {
  plan_version: "learning-orchestrator-v1";
  event_id: string;
  event_revision: number;
  latest_review_id: string | null;
  learning_score: number;
  learning_band: "low" | "medium" | "high" | "priority";
  components: {
    uncertainty: number;
    disagreement: number;
    novelty: number;
    drift: number;
    coverage_gap: number;
    redundancy: number;
    annotation_cost: number;
  };
  reasons: string[];
  intervention_score: number | null;
  intervention_band: string | null;
  drift_state: "insufficient_data" | "stable" | "watch" | "drift";
  routes: LearningRoute[];
  automatic_training: false;
  automatic_promotion: false;
  generated_at: string;
}

export interface DriftMetric {
  name: string;
  baseline: number;
  current: number;
  delta: number;
  points: number;
  detail: string;
}

export interface DriftSnapshot {
  state: "insufficient_data" | "stable" | "watch" | "drift";
  score: number;
  reviewed_events: number;
  baseline_size: number;
  current_size: number;
  minimum_required: number;
  metrics: DriftMetric[];
  mode: "shadow";
  automatic_training: false;
  automatic_promotion: false;
  generated_at: string;
}

export interface LearningRouteSummary {
  use: DevelopmentUse;
  recommended_count: number;
  ready_count: number;
  awaiting_gate_count: number;
  downstream: string;
  safety_gate: string;
}

export interface LearningCandidateSummary {
  event_id: string;
  event_type: string;
  video_id: string;
  learning_score: number;
  learning_band: "low" | "medium" | "high" | "priority";
  intervention_score: number | null;
  recommended_uses: DevelopmentUse[];
  ready_uses: DevelopmentUse[];
  blockers: string[];
}

export type PipelineStage =
  | "review"
  | "approval"
  | "queue"
  | "training"
  | "measurement"
  | "promotion";

export interface PipelineStageSummary {
  stage: PipelineStage;
  count: number;
  blocked_count: number;
  action_label: string;
  detail: string;
}

export interface PipelineEventItem {
  event_id: string;
  event_type: string;
  video_id: string;
  learning_score: number;
  learning_band: "low" | "medium" | "high" | "priority";
  recommended_uses: DevelopmentUse[];
  ready_uses: DevelopmentUse[];
  blockers: string[];
}

export interface LearningRouteItem {
  event_id: string;
  event_revision: number;
  event_type: string;
  review_id: string;
  approval_id: string;
  use: DevelopmentUse;
  learning_score: number;
  learning_band: "low" | "medium" | "high" | "priority";
  downstream: string;
}

export interface PipelineQueueGroup {
  use: DevelopmentUse;
  downstream: string;
  safety_gate: string;
  count: number;
  items: LearningRouteItem[];
}

export type TrainingJobStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "cancelled"
  | "budget_stopped"
  | "interrupted";

export type DfineArchitecture = "dfine_n" | "dfine_s";

export interface TrainingJob {
  job_id: string;
  dataset_id: string;
  dataset_fingerprint: string;
  export_fingerprint: string;
  export_ref: string;
  selection_policy_version: string | null;
  architecture: DfineArchitecture;
  category_names: string[];
  verified_frame_count: number;
  train_frame_count: number;
  validation_frame_count: number;
  source_video_count: number;
  box_count: number;
  dfine_repository_revision: string;
  seed: number;
  epochs: number;
  batch_size: number;
  workers: number;
  gpu_index: number;
  max_gpu_minutes: number;
  daily_gpu_minutes: number;
  status: TrainingJobStatus;
  requested_by: string;
  output_ref: string;
  checkpoint_ref: string | null;
  checkpoint_sha256: string | null;
  started_at: string | null;
  finished_at: string | null;
  elapsed_seconds: number;
  error_code: string | null;
  error_message: string | null;
  created_at: string;
  updated_at: string;
  revision: number;
}

export interface ModelEvaluation {
  evaluation_id: string;
  checkpoint_sha256: string;
  map_50_95: number;
  map_50: number;
  critical_recall: number;
  false_alarms_per_hour: number;
  p95_latency_ms: number;
  peak_memory_mb: number;
  repetitions: number;
  shadow_passed: boolean;
  evaluator: string;
  measured_at: string;
}

export interface ModelVersion {
  model_version_id: string;
  training_job_id: string;
  architecture: DfineArchitecture;
  checkpoint_ref: string;
  checkpoint_sha256: string;
  stage: "candidate" | "champion" | "retired" | "revoked";
  evaluation: ModelEvaluation | null;
  deployment: { onnx_ref: string; onnx_sha256: string } | null;
  promotion_policy_version: string | null;
  approved_by: string | null;
  promotion_reason: string | null;
  created_at: string;
  promoted_at: string | null;
  retired_at: string | null;
}

export interface PipelineModelItem {
  version: ModelVersion;
  gate_failures: string[];
  gate_passed: boolean;
  onnx_exported: boolean;
  measured: boolean;
  shadow_passed: boolean;
}

export interface DfineTrainingPolicy {
  policy_version: string;
  minimum_verified_frames: number;
  minimum_train_frames: number;
  minimum_validation_frames: number;
  minimum_source_videos: number;
  maximum_epochs: number;
  maximum_batch_size: number;
  maximum_workers: number;
  maximum_gpu_minutes_per_job: number;
  maximum_gpu_minutes_per_day: number;
  allowed_architectures: DfineArchitecture[];
}

export interface PromotionPolicy {
  policy_version: string;
  minimum_map_50_95: number;
  minimum_critical_recall: number;
  maximum_false_alarms_per_hour: number;
  maximum_p95_latency_ms: number;
  maximum_peak_memory_mb: number;
  minimum_repetitions: number;
  maximum_critical_recall_drop: number;
  maximum_false_alarm_increase: number;
  maximum_latency_increase_ratio: number;
}

export interface PipelineReadiness {
  can_plan: boolean;
  can_run: boolean;
  blockers: string[];
  active_workload: string | null;
  training_policy_version: string | null;
  promotion_policy_version: string | null;
}

export interface LearningPipelineView {
  pipeline_version: "dortgoz-learning-pipeline-v1";
  stages: PipelineStageSummary[];
  review_items: PipelineEventItem[];
  approval_items: PipelineEventItem[];
  queue: PipelineQueueGroup[];
  jobs: TrainingJob[];
  candidates: PipelineModelItem[];
  champion: PipelineModelItem | null;
  readiness: PipelineReadiness;
  training_policy: DfineTrainingPolicy | null;
  promotion_policy: PromotionPolicy | null;
  drift: DriftSnapshot;
  mode: "human_gated";
  automatic_training: false;
  automatic_promotion: false;
  generated_at: string;
}

export interface BatchApprovalResult {
  approved_event_ids: string[];
  failures: { event_id: string; reason: string }[];
}

export interface LearningOrchestratorOverview {
  orchestrator_version: "dortgoz-learning-orchestrator-v1";
  total_events: number;
  reviewed_events: number;
  pending_review_events: number;
  pending_approval_events: number;
  stale_approval_events: number;
  ready_routes: number;
  route_summaries: LearningRouteSummary[];
  priority_candidates: LearningCandidateSummary[];
  drift: DriftSnapshot;
  mode: "human_gated";
  automatic_execution: false;
  automatic_training: false;
  automatic_promotion: false;
  generated_at: string;
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

export interface VideoMetadata {
  video_id: string;
  stored_filename: string;
  media_path: string;
  original_filename: string;
  duration_seconds: number;
}

export interface EvidenceItem {
  evidence_id: string;
  timestamp: number;
  frame_id: string;
  frame_path: string;
  clip_path: string;
  claim: string;
  validated: boolean;
}

export interface ProcedureAction {
  priority: number;
  action: string;
  document_id: string;
  section: string;
  version: string;
  content_hash: string;
  requires_human_approval: boolean;
}

export interface VerifiedEvent {
  event_id: string;
  status: "confirmed" | "rejected" | "human_review" | "processing_failed";
  event_type: string;
  start_time: number | null;
  peak_time: number | null;
  end_time: number | null;
  confidence: number | null;
  before: string | null;
  during: string | null;
  after: string | null;
  evidence: EvidenceItem[];
  uncertainties: string[];
  risk: { level: string; reasons: string[]; rule_ids: string[]; review_required: boolean } | null;
  actions: ProcedureAction[];
  decision_trace: Array<{ step: number; action: string; reason: string; policy_rule_id: string; tool_name: string | null; success: boolean | null; error_code: string | null }>;
}

export interface AnalysisProgress {
  analysis_id: string;
  status: "queued" | "running" | "completed" | "failed" | "review_required";
  progress: number;
  error: string | null;
}

export interface ProcedureSource {
  document_id: string;
  section: string;
  version: string;
  content_hash: string;
}

export interface QueryResponse {
  answer_tr: string;
  event_refs: string[];
  evidence_refs: string[];
  procedure_sources: ProcedureSource[];
  uncertainties: string[];
}

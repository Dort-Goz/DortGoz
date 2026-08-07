import type { AnalysisProgress, QueryResponse, VerifiedEvent, VideoMetadata } from "../types/domain";

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(path, init);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(body?.error?.message ?? `İstek başarısız (${response.status})`);
  }
  return response.json() as Promise<T>;
}

export function uploadVideo(file: File) {
  const body = new FormData();
  body.append("file", file);
  return request<VideoMetadata>("/api/videos", { method: "POST", body });
}

export function startAnalysis(videoId: string, profile = "mock") {
  return request<{ analysis_id: string }>(`/api/videos/${videoId}/analyze`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ profile }),
  });
}

export const getAnalysis = (analysisId: string) => request<AnalysisProgress>(`/api/analyses/${analysisId}/status`);
export const getEvents = (analysisId: string) => request<VerifiedEvent[]>(`/api/analyses/${analysisId}/events`);
export const getReport = (analysisId: string) => request<Record<string, unknown>>(`/api/reports/${analysisId}`);
export function queryAnalysis(analysisId: string, question: string) {
  return request<QueryResponse>(`/api/analyses/${analysisId}/query`, {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ question }),
  });
}

export function reviewEvent(eventId: string, decision: "confirm" | "reject", note: string) {
  return request(`/api/events/${eventId}/review`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ decision, reviewer: "operator", note }),
  });
}

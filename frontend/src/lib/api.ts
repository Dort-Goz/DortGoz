import type { QueryResponse, VideoMetadata } from "../types/domain";

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

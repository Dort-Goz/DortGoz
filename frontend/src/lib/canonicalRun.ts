import type { OperatorMessage } from "../types/events";

export interface StartGate {
  current: boolean;
}

interface StartCanonicalRunArgs {
  selected: string;
  busy: boolean;
  gate: StartGate;
  overrides: Pick<OperatorMessage, "model" | "system_prompt" | "task_prompt" | "mode">;
  dispatchStarted: (video: string) => void;
  send: (message: OperatorMessage) => void;
}

export function startCanonicalRun({
  selected,
  busy,
  gate,
  overrides,
  dispatchStarted,
  send,
}: StartCanonicalRunArgs): boolean {
  if (!selected || busy || gate.current) return false;
  gate.current = true;
  dispatchStarted(selected);
  send({ kind: "start_run", video: selected, ...overrides });
  return true;
}

export function includeUploadedVideo(videos: string[], storedFilename: string): string[] {
  return videos.includes(storedFilename) ? videos : [...videos, storedFilename];
}

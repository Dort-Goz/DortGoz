import type { Event, OperatorMessage } from "../types/events";

export const DIALOGUE_KEY = "dortgoz.dialogue_id";

interface SessionStorageLike {
  getItem(key: string): string | null;
  setItem(key: string, value: string): void;
}

export function loadDialogueId(
  storage: SessionStorageLike,
  createId: () => string,
): string {
  const current = storage.getItem(DIALOGUE_KEY)?.trim();
  if (current) return current;
  const created = createId();
  storage.setItem(DIALOGUE_KEY, created);
  return created;
}

export function buildChatMessage(
  text: string,
  dialogueId: string,
  feed: string,
  referencedEventId: string,
): OperatorMessage {
  return {
    kind: "chat",
    text,
    dialogue_id: dialogueId,
    feed,
    referenced_event_id: referencedEventId,
  };
}

export function eventBelongsToDialogue(event: Event, dialogueId: string): boolean {
  const payload = event.payload as { dialogue_id?: string };
  return !payload.dialogue_id || payload.dialogue_id === dialogueId;
}

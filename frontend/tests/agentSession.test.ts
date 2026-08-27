import { describe, expect, test } from "bun:test";

import {
  DIALOGUE_KEY,
  buildChatMessage,
  eventBelongsToDialogue,
  loadDialogueId,
  renewDialogueId,
} from "../src/lib/agentSession";
import type { Event } from "../src/types/events";


class MemoryStorage {
  values = new Map<string, string>();

  getItem(key: string): string | null {
    return this.values.get(key) ?? null;
  }

  setItem(key: string, value: string): void {
    this.values.set(key, value);
  }
}


describe("agent oturumu ve bağlamı", () => {
  test("aynı sekme kararlı bir dialogue_id kullanır", () => {
    const storage = new MemoryStorage();
    let sequence = 0;
    const create = () => `dialogue-${++sequence}`;

    expect(loadDialogueId(storage, create)).toBe("dialogue-1");
    expect(loadDialogueId(storage, create)).toBe("dialogue-1");
    expect(storage.getItem(DIALOGUE_KEY)).toBe("dialogue-1");
  });

  test("video değişince yeni dialogue_id üretir", () => {
    const storage = new MemoryStorage();
    storage.setItem(DIALOGUE_KEY, "dialogue-old");

    expect(renewDialogueId(storage, () => "dialogue-new")).toBe("dialogue-new");
    expect(storage.getItem(DIALOGUE_KEY)).toBe("dialogue-new");
  });

  test("video seçimi ve yükleme aynı sohbet sıfırlama yolunu kullanır", async () => {
    const source = await Bun.file(new URL("../src/App.tsx", import.meta.url)).text();

    expect(source).toContain('onChange={(e) => selectVideo(e.target.value)}');
    expect(source).toContain("selectVideo(video.stored_filename);");
    expect(source).toContain('dispatch({ kind: "clear_chat" });');
  });

  test("sohbet mesajı seçili kamera ve olayı taşır", () => {
    expect(buildChatMessage("emin misin?", "dialogue-1", "KAM-2", "inc-7")).toEqual({
      kind: "chat",
      text: "emin misin?",
      dialogue_id: "dialogue-1",
      feed: "KAM-2",
      referenced_event_id: "inc-7",
    });
  });

  test("başka operatörün sohbet ve agent izini göstermez", () => {
    const own = {
      seq: 1,
      ts: 1,
      feed: "KAM-1",
      payload: {
        type: "chat_message",
        role: "agent",
        text: "yanıt",
        streaming: false,
        dialogue_id: "dialogue-1",
      },
    } as Event;
    const other = {
      ...own,
      payload: { ...own.payload, dialogue_id: "dialogue-2" },
    } as Event;
    const pipeline = {
      seq: 2,
      ts: 2,
      feed: "KAM-1",
      payload: { type: "agent_step", node: "interpret", status: "end", detail: "tamam" },
    } as Event;

    expect(eventBelongsToDialogue(own, "dialogue-1")).toBe(true);
    expect(eventBelongsToDialogue(other, "dialogue-1")).toBe(false);
    expect(eventBelongsToDialogue(pipeline, "dialogue-1")).toBe(true);
  });
});

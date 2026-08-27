import { memo, useEffect, useState } from "react";
import type { ActivityStatus, ActivityStrip } from "../types/events";
import { ACTIVITY_WINDOW_SECONDS } from "../state";

export type SlotKind = "bos" | "bekliyor" | "sessiz" | "hareket" | "dikkat" | "anomali";

export interface Slot {
  kind: SlotKind;
  level: number;
  wall: number;
}

const RAMP: Record<SlotKind, readonly string[]> = {
  bos: ["bg-zinc-950"],
  bekliyor: ["bg-zinc-100"],
  sessiz: ["bg-zinc-700"],
  hareket: ["bg-emerald-900", "bg-emerald-700", "bg-emerald-400"],
  dikkat: ["bg-amber-800", "bg-amber-600", "bg-amber-300"],
  anomali: ["bg-red-900", "bg-red-600", "bg-red-400"],
};

const KIND_TR: Record<SlotKind, string> = {
  bos: "kayıt yok",
  bekliyor: "henüz çözümlenmedi",
  sessiz: "eşik altı — hareket yok",
  hareket: "hareket var, olay yok",
  dikkat: "dikkat çeken pencere",
  anomali: "anomali",
};

const STATUS_KIND: Record<ActivityStatus, SlotKind> = {
  sakin: "sessiz",
  eleme: "hareket",
  hareket: "hareket",
  dikkat: "dikkat",
  anomali: "anomali",
};

function stripStart(strip: ActivityStrip): number {
  if (strip.content_start && strip.content_start > 0) return strip.content_start;
  return strip.wall_end - (strip.window_end - strip.window_start);
}

export function buildTimeline(
  strips: ActivityStrip[],
  now: number,
  seconds = ACTIVITY_WINDOW_SECONDS,
): Slot[] {
  const from = now - seconds;
  const slots: Slot[] = Array.from({ length: seconds }, (_, i) => ({
    kind: "bos" as SlotKind,
    level: 0,
    wall: from + i,
  }));
  let newest = 0;
  let oldest = Number.POSITIVE_INFINITY;

  for (const strip of strips) {
    const span = Math.max(strip.window_end - strip.window_start, 0.001);
    const step = span / Math.max(strip.levels.length, 1);
    const base = stripStart(strip);
    oldest = Math.min(oldest, base);
    newest = Math.max(newest, base + span);
    const kind = STATUS_KIND[strip.status] ?? "sessiz";
    strip.levels.forEach((level, index) => {
      const index0 = Math.floor(base + index * step - from);
      if (index0 < 0 || index0 >= seconds) return;
      slots[index0] = {
        kind: kind === "hareket" && level === 0 ? "sessiz" : kind,
        level,
        wall: from + index0,
      };
    });
  }

  for (const slot of slots) {
    if (slot.kind !== "bos") continue;
    if (slot.wall >= newest && newest > 0) slot.kind = "bekliyor";
    else if (slot.wall > oldest && slot.wall < newest) slot.kind = "bekliyor";
  }
  return slots;
}

function tone(slot: Slot): string {
  const shades = RAMP[slot.kind];
  return shades[Math.min(shades.length - 1, Math.max(0, slot.level - 1))];
}

const hhmm = (epoch: number) =>
  new Date(epoch * 1000).toLocaleTimeString("tr-TR", {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });

function ActivityBar({ strips, height = "h-2.5" }: {
  strips: ActivityStrip[];
  height?: string;
}) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, []);

  const slots = buildTimeline(strips, now);
  const pending = slots.filter((s) => s.kind === "bekliyor").length;
  const moving = slots.filter((s) => s.level > 0).length;

  return (
    <div
      className={`flex w-full ${height} overflow-hidden rounded-[2px] bg-zinc-950`}
      title={`Son ${ACTIVITY_WINDOW_SECONDS / 60} dakika · eşiği geçen ${moving} kare`
        + (pending > 0 ? ` · ${pending} sn çözümlenmeyi bekliyor` : "")}
    >
      {slots.map((slot) => (
        <span
          key={slot.wall}
          className={`min-w-0 flex-1 ${tone(slot)}`}
          title={`${hhmm(slot.wall)} · ${KIND_TR[slot.kind]}${
            slot.level > 0 ? ` · yoğunluk ${slot.level}` : ""
          }`}
        />
      ))}
    </div>
  );
}

export default memo(ActivityBar);

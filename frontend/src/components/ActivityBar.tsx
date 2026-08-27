import { memo } from "react";
import type { ActivityStatus, ActivityStrip } from "../types/events";
import { ACTIVITY_WINDOW_SECONDS } from "../state";

const STATUS_TR: Record<ActivityStatus, string> = {
  sakin: "eşik altı — hareket yok",
  eleme: "aday taraması eledi",
  hareket: "hareket var, olay yok",
  dikkat: "dikkat çeken pencere",
  anomali: "anomali",
};

const TONE: Record<ActivityStatus, readonly [string, string, string]> = {
  sakin: ["bg-zinc-800", "bg-zinc-700", "bg-zinc-600"],
  eleme: ["bg-sky-950", "bg-sky-800", "bg-sky-600"],
  hareket: ["bg-emerald-950", "bg-emerald-800", "bg-emerald-500"],
  dikkat: ["bg-amber-900", "bg-amber-700", "bg-amber-400"],
  anomali: ["bg-red-900", "bg-red-600", "bg-red-400"],
};

export interface ActivityCell {
  key: string;
  level: number;
  status: ActivityStatus;
  t: number;
  wall: number;
}

export function toCells(strips: ActivityStrip[], limit = 120): ActivityCell[] {
  const cells: ActivityCell[] = [];
  for (const strip of [...strips].sort((a, b) => a.wall_end - b.wall_end)) {
    const span = Math.max(strip.window_end - strip.window_start, 0.001);
    const step = span / Math.max(strip.levels.length, 1);
    strip.levels.forEach((level, index) => {
      cells.push({
        key: `${strip.wall_end}:${index}`,
        level,
        status: strip.status,
        t: strip.window_start + index * step,
        wall: strip.wall_end - (strip.levels.length - index) * step,
      });
    });
  }
  return cells.length > limit ? cells.slice(cells.length - limit) : cells;
}

const clock = (t: number) =>
  `${String(Math.floor(t / 60)).padStart(2, "0")}:${String(Math.floor(t % 60)).padStart(2, "0")}`;

function ActivityBar({ strips, height = "h-2.5" }: {
  strips: ActivityStrip[];
  height?: string;
}) {
  const cells = toCells(strips);
  if (cells.length === 0) {
    return (
      <div
        className={`flex w-full ${height} items-center justify-center rounded-[2px] bg-zinc-900/80`}
        title={`Son ${ACTIVITY_WINDOW_SECONDS / 60} dakikada işlenmiş pencere yok`}
      />
    );
  }
  return (
    <div
      className={`flex w-full ${height} overflow-hidden rounded-[2px] bg-zinc-900/80`}
      title={`Son ${ACTIVITY_WINDOW_SECONDS / 60} dakika · ${cells.length} kare · `
        + `eşiği geçen ${cells.filter((c) => c.level > 0).length} kare`}
    >
      {cells.map((cell) => (
        <span
          key={cell.key}
          className={`min-w-0 flex-1 ${
            cell.level === 0 ? "bg-zinc-800/70" : TONE[cell.status][Math.min(2, cell.level - 1)]
          }`}
          title={`${clock(cell.t)} · ${STATUS_TR[cell.status]}${
            cell.level === 0 ? " · eşik altı" : ` · yoğunluk ${cell.level}`
          }`}
        />
      ))}
    </div>
  );
}

export default memo(ActivityBar);

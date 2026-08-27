export const RISK_RANK: Record<string, number> = {
  dusuk: 0,
  orta: 1,
  yuksek: 2,
  kritik: 3,
};

export const BAND_RANK: Record<string, number> = {
  routine: 0,
  review: 1,
  high: 2,
  urgent: 3,
};

export interface AlertCandidate {
  key: string;
  risk: string;
  intervention_band: string;
  intervention_score: number;
  wall: number;
}

export function severityRank(item: AlertCandidate): number {
  return Math.max(RISK_RANK[item.risk] ?? 0, BAND_RANK[item.intervention_band] ?? 0);
}

export function outranks(candidate: AlertCandidate, watched: AlertCandidate): boolean {
  const gap = severityRank(candidate) - severityRank(watched);
  if (gap !== 0) return gap > 0;
  return candidate.intervention_score > watched.intervention_score;
}

export function unseenAlerts<T extends AlertCandidate>(
  pending: T[],
  seen: ReadonlySet<string>,
  limit = 4,
): T[] {
  return pending
    .filter((item) => !seen.has(item.key))
    .sort((a, b) => severityRank(b) - severityRank(a) || b.wall - a.wall)
    .slice(0, limit);
}

export function shouldChime(alerts: AlertCandidate[]): boolean {
  return alerts.some((item) => severityRank(item) >= RISK_RANK.kritik);
}

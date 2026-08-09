import type { AnomalyType, CanonicalEventType, Risk } from "../types/events";

/** Canonical internal tiplerin operatöre görünen Türkçe adları. */
export const CANONICAL_TYPE_TR: Record<CanonicalEventType, string> = {
  normal: "olağan",
  uncertain: "belirsiz",
  unknown_anomaly: "sınıflandırılamayan anomali",
  physical_fight: "fiziksel kavga",
  assault: "saldırı şüphesi",
  possible_theft: "olası hırsızlık",
  possible_armed_incident: "silaha benzer nesne içeren olası olay",
  fire_smoke: "yangın veya duman",
  explosion: "patlama",
  vehicle_collision: "araç çarpışması",
  vandalism: "vandalizm",
};

/** WS wire contract eski Türkçe değerleri taşımaya devam eder. */
export const LEGACY_ANOMALY_TO_CANONICAL: Record<AnomalyType, CanonicalEventType> = {
  kavga: "physical_fight",
  saldiri: "assault",
  hirsizlik: "possible_theft",
  silahli_olay: "possible_armed_incident",
  yangin: "fire_smoke",
  patlama: "explosion",
  arac_kazasi: "vehicle_collision",
  vandalizm: "vandalism",
  normal: "normal",
  bilinmeyen: "unknown_anomaly",
};

/** Eski bileşenler için canonical label'dan türetilen uyumluluk görünümü. */
export const TYPE_TR: Record<AnomalyType, string> = {
  kavga: CANONICAL_TYPE_TR[LEGACY_ANOMALY_TO_CANONICAL.kavga],
  saldiri: CANONICAL_TYPE_TR[LEGACY_ANOMALY_TO_CANONICAL.saldiri],
  hirsizlik: CANONICAL_TYPE_TR[LEGACY_ANOMALY_TO_CANONICAL.hirsizlik],
  silahli_olay: CANONICAL_TYPE_TR[LEGACY_ANOMALY_TO_CANONICAL.silahli_olay],
  yangin: CANONICAL_TYPE_TR[LEGACY_ANOMALY_TO_CANONICAL.yangin],
  patlama: CANONICAL_TYPE_TR[LEGACY_ANOMALY_TO_CANONICAL.patlama],
  arac_kazasi: CANONICAL_TYPE_TR[LEGACY_ANOMALY_TO_CANONICAL.arac_kazasi],
  vandalizm: CANONICAL_TYPE_TR[LEGACY_ANOMALY_TO_CANONICAL.vandalizm],
  normal: CANONICAL_TYPE_TR[LEGACY_ANOMALY_TO_CANONICAL.normal],
  bilinmeyen: CANONICAL_TYPE_TR[LEGACY_ANOMALY_TO_CANONICAL.bilinmeyen],
};

/** Risk değerleri ASCII enum (dusuk/yuksek) — operatör Türkçesini burada alır. */
export const RISK_TR: Record<Risk, string> = {
  dusuk: "düşük", orta: "orta", yuksek: "yüksek", kritik: "kritik",
};

export const PHASE_TR: Record<string, string> = {
  basladi: "başladı", gelisiyor: "gelişiyor", sonuclandi: "sonuçlandı",
};

/** Ajan izleme düğümlerinin operatörce adları (detay kipi ham adları korur). */
export const NODE_TR: Record<string, string> = {
  perceive: "algı", triage: "eleme", interpret: "yorum", ledger: "defter",
  oversight: "gözetmen", tools: "araç", respond: "yanıt",
};

/** Mühendis ölçümlerini (token/PP/P(dikkat)) satırdan ayıklar. */
export function stripPerf(detail: string): string {
  return detail
    .replace(/ · P\(dikkat\)=[\d.]+/g, "")
    .replace(/ · \d+\+\d+\/\d+ tok.*$/g, "")
    .replace(/ · \d+\+\d+ tok.*$/g, "");
}

/** Backend izleme satırları legacy WS değerlerini yazar; UI canonical label gösterir. */
const ENUM_TR: [RegExp, string][] = [
  ...Object.entries(TYPE_TR).map(([k, v]) => [new RegExp(`\\b${k}\\b`, "g"), v] as [RegExp, string]),
  ...Object.entries(RISK_TR).map(([k, v]) => [new RegExp(`\\b${k}\\b`, "g"), v] as [RegExp, string]),
];

export function humanizeEnums(detail: string): string {
  let out = detail;
  for (const [re, tr] of ENUM_TR) out = out.replace(re, tr);
  return out;
}

export function clock(t: number) {
  const m = Math.floor(t / 60), s = Math.floor(t % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

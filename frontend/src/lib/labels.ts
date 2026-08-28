import type { AnomalyType, CanonicalEventType, Risk } from "../types/events";

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

export const TYPE_TR = Object.fromEntries(
  Object.entries(LEGACY_ANOMALY_TO_CANONICAL).map(([legacy, canonical]) => [
    legacy,
    CANONICAL_TYPE_TR[canonical],
  ]),
) as Record<AnomalyType, string>;

/** Kategori kimliğini okunur ada çevirir; kanonik ve eski adları birlikte kabul eder. */
export function categoryLabel(category: string): string {
  return CANONICAL_TYPE_TR[category as keyof typeof CANONICAL_TYPE_TR]
    ?? TYPE_TR[category as keyof typeof TYPE_TR]
    ?? category;
}

export const RISK_TR: Record<Risk, string> = {
  dusuk: "düşük", orta: "orta", yuksek: "yüksek", kritik: "kritik",
};

const SEVERITY_TR: Record<string, string> = {
  dusuk: "Düşük", low: "Düşük",
  orta: "Orta", medium: "Orta",
  yuksek: "Yüksek", high: "Yüksek",
  kritik: "Kritik", critical: "Kritik",
  routine: "Rutin", review: "İncelenmeli", urgent: "Acil",
  review_required: "İnceleme", undetermined: "Belirsiz",
};

const SEVERITY_CLS: Record<string, string> = {
  dusuk: "chip-dusuk", low: "chip-dusuk",
  orta: "chip-orta", medium: "chip-orta",
  review: "chip-orta", review_required: "chip-orta",
  yuksek: "chip-yuksek", high: "chip-yuksek",
  kritik: "chip-kritik", critical: "chip-kritik", urgent: "chip-kritik",
  routine: "chip-notr", undetermined: "chip-notr",
};

export const severityLabel = (key: string): string => SEVERITY_TR[key] ?? key;

export const severityClass = (key: string): string => SEVERITY_CLS[key] ?? "chip-notr";

export const PHASE_TR: Record<string, string> = {
  basladi: "başladı", gelisiyor: "gelişiyor", sonuclandi: "sonuçlandı",
};

export const NODE_TR: Record<string, string> = {
  perceive: "algı", triage: "eleme", interpret: "yorum", ledger: "defter",
  oversight: "gözetmen", hakem: "hakem", tools: "araç", respond: "yanıt",
  hazirlik: "hazırlık", tarama: "tarama", pencere_analizi: "pencere analizi",
  dogrulama: "doğrulama", aksiyon: "aksiyon",
};

const TOOL_TR: Record<string, string> = {
  pencere_sorgula: "pencere sorgula",
  ikinci_gorus_al: "ikinci görüş al",
  kanit_klibi_olustur: "kanıt klibi oluştur",
  olay_raporu_olustur: "olay raporu oluştur",
  olayi_aydinlat: "olayı aydınlat",
  olayi_vurgula: "olayı vurgula",
  videoya_git: "videoya git",
  yeniden_incele: "yeniden incele",
  prosedur_sorgula: "prosedür sorgula",
  aksiyon_durumunu_sorgula: "aksiyon durumunu sorgula",
  aktuator_calistir: "aktüatör çalıştır",
  aktuator_durumu_sorgula: "aktüatör durumu sorgula",
  emniyet_bildirimi_hazirla: "emniyet bildirimi hazırla",
  acil_saglik_bildirimi_hazirla: "acil sağlık bildirimi hazırla",
  guvenlik_uyarisi_hazirla: "güvenlik uyarısı hazırla",
  alan_guvenligi_iste: "alan güvenliği iste",
};

export const toolLabel = (name: string): string =>
  TOOL_TR[name] ?? name.replace(/_/g, " ");

export function stripPerf(detail: string): string {
  return detail
    .replace(/ · P\(dikkat\)=[\d.]+/g, "")
    .replace(/ · \d+\+\d+\/(?:\d+|\?) tok.*$/g, "")
    .replace(/ · \d+\+\d+ tok.*$/g, "")
    .replace(/ · PP \d+ ?\/ ?gen \d+ t\/s.*$/g, "");
}

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

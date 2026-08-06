import type { AnomalyType, Risk } from "../types/events";

/** A1 taksonomisinin operatöre görünen adları (şema değerleri makine tarafı). */
export const TYPE_TR: Record<AnomalyType, string> = {
  kavga: "kavga",
  saldiri: "saldırı",
  hirsizlik: "hırsızlık",
  silahli_olay: "silahlı olay",
  yangin: "yangın",
  patlama: "patlama",
  arac_kazasi: "araç kazası",
  vandalizm: "vandalizm",
  normal: "olağan",
  bilinmeyen: "sınıflandırılamayan",
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

/** Mühendis ölçümlerini (token/PP/P(dikkat)) satırdan ayıklar — kompakt kipte
 *  operatör "2 olay · araç kazası" görür; ölçümler detay kipinde durur. */
export function stripPerf(detail: string): string {
  return detail
    .replace(/ · P\(dikkat\)=[\d.]+/g, "")
    .replace(/ · \d+\+\d+\/\d+ tok.*$/g, "")
    .replace(/ · \d+\+\d+ tok.*$/g, "");
}

export function clock(t: number) {
  const m = Math.floor(t / 60), s = Math.floor(t % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

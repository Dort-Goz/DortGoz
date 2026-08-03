import type { AnomalyType } from "../types/events";

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

export const PHASE_TR: Record<string, string> = {
  basladi: "başladı", gelisiyor: "gelişiyor", sonuclandi: "sonuçlandı",
};

export function clock(t: number) {
  const m = Math.floor(t / 60), s = Math.floor(t % 60);
  return `${String(m).padStart(2, "0")}:${String(s).padStart(2, "0")}`;
}

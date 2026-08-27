import { describe, expect, test } from "bun:test";
import { humanizeReason } from "../src/components/TriagePanel";

describe("humanizeReason", () => {
  test("ayrı gerekçeleri birleştirmez", () => {
    // Eski sürüm bunları boşlukla birleştiriyordu ve operatöre
    // "...oturmadı Model emin değil: ..." diye okunmaz tek satır çıkıyordu.
    const parts = humanizeReason(
      "olay kapalı sınıf listesine oturmadı · model belirsizlik bildirdi: "
        + "Kamera açısı 13. saniyede değişti",
    );
    expect(parts).toHaveLength(2);
    expect(parts[0]).toBe("olay kapalı sınıf listesine oturmadı.");
    expect(parts[1]).toStartWith("Model emin değil:");
  });

  test("her madde noktalama ile biter", () => {
    for (const part of humanizeReason("bir gerekçe · ikinci gerekçe.")) {
      expect(part).toMatch(/[.!?…]$/);
    }
  });

  test("teknik doğrulama metnini Türkçeleştirir", () => {
    const [only] = humanizeReason("Runtime evidence VALIDATED provisional");
    expect(only).toBe("Sistem kanıtı doğruladı; otomatik onay kapalı — karar sizde.");
  });

  test("boş parçaları atar", () => {
    expect(humanizeReason("tek gerekçe ·  · ")).toEqual(["tek gerekçe."]);
  });
});

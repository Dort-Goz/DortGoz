import { describe, expect, test } from "bun:test";

import {
  investigationQuestionsFor,
  investigationRequestText,
  investigationVisibleText,
} from "../src/lib/investigationQuestions";
import type { AnomalyType, IncidentUpdate } from "../src/types/events";

function incident(anomalyType: AnomalyType, title: string): IncidentUpdate {
  return {
    type: "incident_update",
    incident_id: "inc-1",
    t: 10,
    phase: "sonuclandi",
    title,
    anomaly_type: anomalyType,
    risk: "orta",
    detail: "",
    boxes: [],
  };
}

describe("olay aydınlatma soruları", () => {
  test("her olay üç genel ve iki kategori sorusu gösterir", () => {
    const result = investigationQuestionsFor(incident("arac_kazasi", "Araç kazası"));

    expect(result.questions).toHaveLength(5);
    expect(result.questions.filter((question) => question.scope === "general")).toHaveLength(3);
    expect(result.questions.filter((question) => question.scope === "category")).toHaveLength(2);
  });

  test("beş sorunun ayrıntılı istemini gönderir, operatöre kısa soruyu gösterir", () => {
    const questions = investigationQuestionsFor(incident("arac_kazasi", "Araç kazası"))
      .questions;

    expect(questions).toHaveLength(5);
    for (const question of questions) {
      const requestText = investigationRequestText(question);
      expect(requestText).toBe(`Olayı aydınlat: ${question.prompt}`);
      expect(investigationVisibleText(requestText)).toBe(`Olayı aydınlat: ${question.label}`);
    }
  });

  test("serbest operatör mesajlarını değiştirmez", () => {
    const text = "Bu olaydaki en güçlü kanıt nedir?";
    expect(investigationVisibleText(text)).toBe(text);
  });

  test("birleşik hırsızlık sınıfında belirgin alt odağı seçer", () => {
    expect(
      investigationQuestionsFor(incident("hirsizlik", "Shoplifting: ürün raftan alındı"))
        .profileId,
    ).toBe("shoplifting");
    expect(
      investigationQuestionsFor(incident("hirsizlik", "Kapı zorlamalı Burglary olayı"))
        .profileId,
    ).toBe("burglary");
    expect(
      investigationQuestionsFor(incident("hirsizlik", "Tehdit içeren Robbery olayı"))
        .profileId,
    ).toBe("robbery");
  });

  test("alt tür belirsizse güvenli üst kategori sorularına döner", () => {
    const result = investigationQuestionsFor(incident("hirsizlik", "Olası hırsızlık"));

    expect(result.profileId).toBe("theft_generic");
    expect(result.questions[3].label).toBe("Eşya hangi koşulda alındı?");
  });

  test("UCF Abuse ve Arrest profillerini üretim sınıfları içinde tanır", () => {
    expect(investigationQuestionsFor(incident("kavga", "Abuse olayı")).profileId).toBe("abuse");
    expect(investigationQuestionsFor(incident("bilinmeyen", "Arrest görüntüsü")).profileId)
      .toBe("arrest");
  });

  test("UCF-Crime'ın 13 olay odağını uygun üretim sınıfında tanır", () => {
    const cases: Array<[AnomalyType, string, string]> = [
      ["kavga", "Abuse", "abuse"],
      ["bilinmeyen", "Arrest", "arrest"],
      ["yangin", "Arson", "arson"],
      ["saldiri", "Assault", "assault"],
      ["hirsizlik", "Burglary", "burglary"],
      ["patlama", "Explosion", "explosion"],
      ["kavga", "Fighting", "fighting"],
      ["arac_kazasi", "RoadAccidents", "road_accident"],
      ["hirsizlik", "Robbery", "robbery"],
      ["silahli_olay", "Shooting", "shooting"],
      ["hirsizlik", "Shoplifting", "shoplifting"],
      ["hirsizlik", "Stealing", "stealing"],
      ["vandalizm", "Vandalism", "vandalism"],
    ];

    for (const [anomalyType, title, expected] of cases) {
      expect(investigationQuestionsFor(incident(anomalyType, title)).profileId).toBe(expected);
    }
  });

  test("sorular suçluluk veya kimlik hükmü istemez", () => {
    const prompts = investigationQuestionsFor(incident("silahli_olay", "Shooting"))
      .questions.map((question) => question.prompt).join(" ");

    expect(prompts).toContain("Kimlik veya suçluluk çıkarımı yapma");
    expect(prompts).toContain("görsel kanıt yoksa atış sayısı verme");
  });
});

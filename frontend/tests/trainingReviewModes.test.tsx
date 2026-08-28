import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import FineTuneDecisionDialog from "../src/components/FineTuneDecisionDialog";
import MaintenanceReviewDialog from "../src/components/MaintenanceReviewDialog";
import TrainingReviewPanel from "../src/components/TrainingReviewPanel";

describe("olay inceleme ve bakım görevleri", () => {
  test("olay inceleme operatör kararını gösterir", () => {
    const markup = renderToStaticMarkup(
      <TrainingReviewPanel
        user="operator"
        eventId="event-1"
        mode="review"
        onClose={() => {}}
      />,
    );

    expect(markup).toContain("Olayı İncele");
    expect(markup).toContain("Evet, doğru");
    expect(markup).toContain("Hayır, düzenle");
    expect(markup).toContain("Anomali yok");
  });

  test("IT incelemesi operatör kararından ayrı ekrandadır", () => {
    const markup = renderToStaticMarkup(
      <MaintenanceReviewDialog
        user="it-operator"
        eventId="event-1"
        onClose={() => {}}
        onSaved={() => {}}
      />,
    );

    expect(markup).toContain("IT İncelemesi");
    expect(markup).toContain("Operatör kararı");
    expect(markup).toContain("Anomali doğru");
    expect(markup).toContain("Kategori değiştir");
    expect(markup).toContain("Anomali yok");
    expect(markup).not.toContain("Fine-tune'a gönder");
  });

  test("fine-tune kararı ayrı kabul ve ret düğmeleri taşır", () => {
    const markup = renderToStaticMarkup(
      <FineTuneDecisionDialog
        user="it-operator"
        eventId="event-1"
        onClose={() => {}}
        onSaved={() => {}}
      />,
    );

    expect(markup).toContain("Fine-tune Kararı");
    expect(markup).toContain("İstemiyorum");
    expect(markup).toContain("Fine-tune&#x27;a gönder");
    expect(markup).not.toContain("Kareleri hazırla");
  });
});

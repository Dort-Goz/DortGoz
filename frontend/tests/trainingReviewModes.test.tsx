import { describe, expect, test } from "bun:test";
import { renderToStaticMarkup } from "react-dom/server";

import TrainingReviewPanel from "../src/components/TrainingReviewPanel";

const render = (mode: "review" | "maintenance") => renderToStaticMarkup(
  <TrainingReviewPanel
    user="operator"
    eventId="event-1"
    mode={mode}
    onClose={() => {}}
  />,
);

describe("olay inceleme ve bakım görev kipleri", () => {
  test("olay inceleme yalnız insan kararı araçlarını gösterir", () => {
    const markup = render("review");

    expect(markup).toContain("Olayı İncele");
    expect(markup).toContain("Evet, doğru");
    expect(markup).toContain("Hayır, düzenle");
    expect(markup).toContain("Anomali yok");
    expect(markup).not.toContain("Geliştirme onayı");
    expect(markup).not.toContain("Kareleri hazırla");
  });

  test("bakım yalnız öğrenme ve model yaşam döngüsü araçlarını gösterir", () => {
    const markup = render("maintenance");

    expect(markup).toContain("IT Bakım Kaydı");
    expect(markup).toContain("Fine-tune ve değerlendirme onayı");
    expect(markup).toContain("Kareleri hazırla");
    expect(markup).not.toContain("Evet, doğru");
    expect(markup).not.toContain("Hayır, düzenle");
    expect(markup).not.toContain("Anomali yok");
  });
});

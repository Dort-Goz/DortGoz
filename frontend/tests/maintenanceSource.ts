import { readFileSync } from "node:fs";

/**
 * Bakım ekranı kabuk, IT incelemesi, fine-tune kararı ve yaşam döngüsü
 * bileşenlerine ayrılır. Sözleşme denetimleri hepsini birlikte okur.
 */
export function maintenanceSource(): string {
  return [
    "../src/components/ModelMaintenancePanel.tsx",
    "../src/components/MaintenanceReviewDialog.tsx",
    "../src/components/FineTuneDecisionDialog.tsx",
    "../src/components/MaintenanceStages.tsx",
  ]
    .map((path) => readFileSync(new URL(path, import.meta.url), "utf8"))
    .join("\n");
}

import { readFileSync } from "node:fs";

/**
 * Bakım ekranı iki dosyaya bölünür: kabuk ile inceleme/onay aşamaları
 * ModelMaintenancePanel'de, kuyruk/eğitim/ölçüm/terfi MaintenanceStages'te
 * durur. Sözleşme denetimleri ikisini birlikte okur.
 */
export function maintenanceSource(): string {
  return [
    "../src/components/ModelMaintenancePanel.tsx",
    "../src/components/MaintenanceStages.tsx",
  ]
    .map((path) => readFileSync(new URL(path, import.meta.url), "utf8"))
    .join("\n");
}

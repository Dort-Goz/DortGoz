/**
 * Model metni (başlık, gözlem, gerekçe) kaç satır olacağı belli değildir.
 * Kesme işi arayüzde yapılır: metin tam gelir, kutuya sığdığı kadarı görünür,
 * tamamı ipucunda durur. Sunucu tarafında kısaltmayın — kesilen metin geri
 * gelmez ve ipucu da kesik metni gösterir.
 */
const LINES: Record<number, string> = {
  1: "line-clamp-1",
  2: "line-clamp-2",
  3: "line-clamp-3",
  4: "line-clamp-4",
};

export default function ClampText({
  text, lines = 2, expanded = false, className = "",
}: {
  text: string;
  lines?: 1 | 2 | 3 | 4;
  /** Seçili kart gibi yerlerde tam metni açar. */
  expanded?: boolean;
  className?: string;
}) {
  if (!text) return null;
  return (
    <div
      title={text}
      className={`${expanded ? "whitespace-pre-line" : LINES[lines]} ${className}`}
    >
      {text}
    </div>
  );
}

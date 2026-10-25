import { useState, type MouseEvent } from "react";
import { downloadActPdf, type ActBundle } from "../../infrastructure/act";

/** Кнопка «Акт о нарушении»: собирает акт и скачивает PDF; пока готовится — неактивна. */
export default function ActPdfButton({
  id,
  preloaded,
  className,
  label = "Акт о нарушении (PDF)",
}: {
  id: string;
  preloaded?: ActBundle;
  className?: string;
  label?: string;
}) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const download = (ev: MouseEvent) => {
    ev.stopPropagation(); // кнопка в строке журнала — строку не открываем
    setBusy(true);
    setError(null);
    downloadActPdf(id, preloaded)
      .catch((e: Error) => setError(`Акт не сформирован: ${e.message}`))
      .finally(() => setBusy(false));
  };

  return (
    <>
      <button
        type="button"
        className={[className, busy && "busy"].filter(Boolean).join(" ") || undefined}
        disabled={busy}
        onClick={download}
        title={error ?? "Скачать акт о нарушении в PDF"}
      >
        {busy ? "Готовлю PDF…" : label}
      </button>
      {error && (
        <span className="error" role="alert">
          {error}
        </span>
      )}
    </>
  );
}

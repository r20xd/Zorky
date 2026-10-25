import { actDocument, actFileName } from "../domain/actDocument";
import { snapshotSrc } from "../domain/events";
import type { Camera, ViolationEvent } from "../domain/types";
import { listCameras, listEvents } from "./api";

export interface ActBundle {
  event: ViolationEvent | null;
  cameras: Camera[];
}

/** Всё для акта одним запросом; отдельного GET /api/events/{id} нет — ищем в последних 1000 событиях. */
export async function loadAct(id: string): Promise<ActBundle> {
  const [events, cameras] = await Promise.all([
    listEvents(1000),
    listCameras().catch(() => [] as Camera[]),
  ]);
  return { event: events.find((e) => e.id === id) ?? null, cameras };
}

/** Кадр с красной рамкой нарушения как JPEG data URL; кадра нет или не загрузился — undefined. */
export async function snapshotWithBox(url: string, bbox: ViolationEvent["bbox"]): Promise<string | undefined> {
  try {
    const res = await fetch(url);
    if (!res.ok) return undefined;
    const bitmap = await createImageBitmap(await res.blob());
    const canvas = document.createElement("canvas");
    canvas.width = bitmap.width;
    canvas.height = bitmap.height;
    const ctx = canvas.getContext("2d");
    if (!ctx) return undefined;
    ctx.drawImage(bitmap, 0, 0);
    const [x1, y1, x2, y2] = bbox; // нормировано к 0..1
    ctx.strokeStyle = "#e00000";
    ctx.lineWidth = Math.max(3, Math.round(canvas.width / 200));
    ctx.strokeRect(x1 * canvas.width, y1 * canvas.height, (x2 - x1) * canvas.width, (y2 - y1) * canvas.height);
    return canvas.toDataURL("image/jpeg", 0.9);
  } catch {
    return undefined;
  }
}

type PdfMake = typeof import("pdfmake/build/pdfmake");

// pdfmake (~1 МБ со шрифтами) грузим только при первом скачивании акта
let pdfMakeReady: Promise<PdfMake> | null = null;
function loadPdfMake(): Promise<PdfMake> {
  pdfMakeReady ??= Promise.all([import("pdfmake/build/pdfmake"), import("pdfmake/build/vfs_fonts")]).then(
    ([lib, fonts]) => {
      const pdfMake = ((lib as { default?: PdfMake }).default ?? lib) as PdfMake;
      const vfs = (fonts as { default?: Parameters<PdfMake["addVirtualFileSystem"]>[0] }).default ?? fonts;
      pdfMake.addVirtualFileSystem(vfs as Parameters<PdfMake["addVirtualFileSystem"]>[0]);
      return pdfMake;
    },
  );
  pdfMakeReady.catch(() => (pdfMakeReady = null)); // сбой загрузки — попробуем снова при следующем нажатии
  return pdfMakeReady;
}

/** Собирает акт о нарушении и скачивает его PDF-документом (A4). */
export async function downloadActPdf(id: string, preloaded?: ActBundle): Promise<void> {
  const [bundle, pdfMake] = await Promise.all([preloaded ?? loadAct(id), loadPdfMake()]);
  const { event, cameras } = bundle;
  if (!event) throw new Error(`Событие ${id} не найдено среди последних 1000`);
  const src = snapshotSrc(event);
  const image = src ? await snapshotWithBox(src, event.bbox) : undefined;
  const doc = actDocument({
    event,
    camera: cameras.find((c) => c.id === event.camera_id),
    generated: new Date().toISOString(),
    image,
  });
  await pdfMake.createPdf(doc).download(actFileName(id));
}

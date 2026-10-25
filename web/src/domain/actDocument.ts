import type { Column, Content, TDocumentDefinitions, TableCell } from "pdfmake/interfaces";
import { STATUS_LABELS, SEVERITY_LABELS, severityOf } from "./events";
import { formatDuration, historyRows } from "./journal";
import { REASON_LABELS } from "./queue";
import { VIOLATION_LABELS, type Camera, type ViolationEvent } from "./types";

export interface ActData {
  event: ViolationEvent;
  camera?: Camera;
  /** Когда акт сформирован, ISO. */
  generated: string;
  /** Кадр с рамкой нарушения, data URL (jpeg/png); нет — «кадр недоступен». */
  image?: string;
}

const when = (ts: string) => new Date(ts).toLocaleString("ru-RU", { hour12: false });

/** Имя файла акта: только безопасные символы id. */
export const actFileName = (id: string) => `akt-${id.replace(/[^\w.-]+/g, "_")}.pdf`;

// тонкие серые линии таблиц, как в бланке
const GRID = {
  hLineWidth: () => 0.5,
  vLineWidth: () => 0.5,
  hLineColor: () => "#888888",
  vLineColor: () => "#888888",
  paddingLeft: () => 5,
  paddingRight: () => 5,
  paddingTop: () => 3,
  paddingBottom: () => 3,
};

const section = (n: number, title: string): Content => ({ text: `${n}. ${title}`, style: "section" });

const fieldsTable = (rows: [string, string][]): Content => ({
  table: {
    widths: [150, "*"],
    body: rows.map(([k, v]): TableCell[] => [
      { text: k, bold: true, fillColor: "#f2f2f2" },
      v,
    ]),
  },
  layout: GRID,
});

/** Описание PDF-акта о нарушении (A4, книжная) для pdfmake: шапка, сведения, кадр, реакция, подписи. */
export function actDocument({ event, camera, generated, image }: ActData): TDocumentDefinitions {
  const id = event.id;

  const facts: [string, string][] = [
    ["Нарушение", VIOLATION_LABELS[event.type] ?? event.type],
    ["Критичность", SEVERITY_LABELS[severityOf(event)]],
    ["Камера", camera?.name ?? event.camera_id],
    ["Участок", camera?.area || "—"],
    ["Зона", event.zone_id ?? "—"],
    ["Впервые зафиксировано", when(event.first_seen || event.ts)],
    ["Последний раз", when(event.last_seen || event.ts)],
    ["Повторов", String(event.count)],
    ["Уверенность модели", `${Math.round(event.confidence * 100)}%`],
    ["Текущий статус", STATUS_LABELS[event.status]],
  ];

  const history = historyRows(event);
  const reaction: Content = history.length
    ? {
        table: {
          headerRows: 1,
          widths: [100, "*", 90, 70],
          body: [
            ["Время", "Статус", "Кто", "Через"].map((h): TableCell => ({ text: h, bold: true, fillColor: "#f2f2f2" })),
            ...history.map(({ change, afterMs }): TableCell[] => [
              when(change.ts),
              STATUS_LABELS[change.status] + (change.reason ? ` (${REASON_LABELS[change.reason]})` : ""),
              change.by,
              formatDuration(afterMs),
            ]),
          ],
        },
        layout: GRID,
      }
    : { text: "Статус не менялся.", italics: true };

  const sign = (role: string): Content => ({
    margin: [0, 18, 0, 0],
    columns: [
      { width: 110, text: role, bold: true, margin: [0, 12, 0, 0] },
      signLine("подпись", 110),
      signLine("ФИО", 160),
      signLine("дата", 75),
    ],
    columnGap: 12,
  });

  return {
    pageSize: "A4",
    pageMargins: [56, 60, 48, 56],
    info: { title: `Акт о нарушении № ${id}`, subject: "Акт о нарушении требований промышленной безопасности" },
    defaultStyle: { font: "Roboto", fontSize: 10, lineHeight: 1.2 },
    styles: {
      title: { fontSize: 14, bold: true, alignment: "center" },
      subtitle: { fontSize: 10.5, alignment: "center", margin: [0, 4, 0, 0] },
      section: { fontSize: 11, bold: true, margin: [0, 16, 0, 6] },
      note: { fontSize: 8.5, color: "#555555" },
    },
    header: () => ({
      margin: [56, 24, 48, 0],
      columns: [
        { text: "Система контроля нарушений промбезопасности", style: "note" },
        { text: `Сформирован ${when(generated)}`, style: "note", alignment: "right" },
      ],
    }),
    footer: (page, pages) => ({
      margin: [56, 16, 48, 0],
      columns: [
        { text: `Акт № ${id}`, style: "note" },
        { text: `Стр. ${page} из ${pages}`, style: "note", alignment: "right" },
      ],
    }),
    content: [
      { text: "АКТ", style: "title" },
      { text: "о нарушении требований промышленной безопасности", style: "title", fontSize: 12 },
      { text: `№ ${id} от ${when(event.first_seen || event.ts)}`, style: "subtitle" },
      {
        canvas: [{ type: "line", x1: 0, y1: 0, x2: 491, y2: 0, lineWidth: 0.8 }],
        margin: [0, 10, 0, 0],
      },

      section(1, "Сведения о нарушении"),
      fieldsTable(facts),

      section(2, "Фотофиксация"),
      image
        ? {
            stack: [
              { image, fit: [491, 300], alignment: "center" },
              { text: "Кадр с камеры, красной рамкой отмечена область нарушения", style: "note", alignment: "center", margin: [0, 4, 0, 0] },
            ],
            unbreakable: true,
          }
        : { text: "Кадр недоступен.", italics: true },

      section(3, "Реакция"),
      reaction,

      {
        text: "Акт сформирован автоматически и поддерживает фиксацию и расследование нарушения.",
        style: "note",
        margin: [0, 10, 0, 0],
      },

      {
        stack: [section(4, "Подписи"), sign("Мастер участка"), sign("Специалист по ОТ")],
        unbreakable: true,
      },
    ],
  };
}

// полоса A4 без полей — 491 pt: подписи 110 + 110 + 160 + 75 и три зазора по 12
function signLine(caption: string, width: number): Column {
  return {
    width,
    stack: [
      { canvas: [{ type: "line", x1: 0, y1: 22, x2: width, y2: 22, lineWidth: 0.5 }] },
      { text: caption, style: "note", alignment: "center", margin: [0, 2, 0, 0] },
    ],
  };
}

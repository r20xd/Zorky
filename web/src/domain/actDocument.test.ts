import { describe, expect, it } from "vitest";
import { actDocument, actFileName, type ActData } from "./actDocument";
import type { ViolationEvent } from "./types";

const event = (over: Partial<ViolationEvent> = {}): ViolationEvent => ({
  id: "evt-000042",
  camera_id: "cam-1",
  ts: "2026-10-24T09:00:00Z",
  frame_id: 10,
  type: "no_helmet",
  bbox: [0.1, 0.2, 0.3, 0.4],
  confidence: 0.87,
  track_id: 3,
  zone_id: "z-1",
  severity: "high",
  incident_id: "inc-1",
  count: 2,
  first_seen: "2026-10-24T09:00:00Z",
  last_seen: "2026-10-24T09:01:00Z",
  status: "new",
  status_history: [],
  received_at: "2026-10-24T09:00:01Z",
  ...over,
});

const data = (over: Partial<ActData> = {}): ActData => ({
  event: event(),
  camera: { id: "cam-1", name: "Цех 1, вход", area: "Цех 1" },
  generated: "2026-10-24T10:05:00Z",
  ...over,
});

const text = (over: Partial<ActData> = {}) => JSON.stringify(actDocument(data(over)).content);

describe("actFileName", () => {
  it("оставляет в имени файла только безопасные символы", () => {
    expect(actFileName("evt-000042")).toBe("akt-evt-000042.pdf");
    expect(actFileName("a/b c..d")).toBe("akt-a_b_c..d.pdf");
  });
});

describe("actDocument", () => {
  it("A4 с номером акта в заголовке и свойствах файла", () => {
    const doc = actDocument(data());
    expect(doc.pageSize).toBe("A4");
    expect(doc.info?.title).toContain("evt-000042");
    expect(text()).toContain("№ evt-000042");
  });

  it("сведения: подпись нарушения, камера, участок, уверенность", () => {
    const t = text();
    expect(t).toContain("Нет каски");
    expect(t).toContain("Цех 1, вход");
    expect(t).toContain("87%");
  });

  it("без кадра — «Кадр недоступен.», с кадром — картинка", () => {
    expect(text()).toContain("Кадр недоступен.");
    const t = text({ image: "data:image/jpeg;base64,AAAA" });
    expect(t).not.toContain("Кадр недоступен.");
    expect(t).toContain('"image":"data:image/jpeg;base64,AAAA"');
  });

  it("реакция: пустая история и строка с причиной ложного", () => {
    expect(text()).toContain("Статус не менялся.");
    const t = text({
      event: event({ status: "false", status_history: [{ status: "false", ts: "2026-10-24T09:02:00Z", by: "operator", reason: "duplicate" }] }),
    });
    expect(t).not.toContain("Статус не менялся.");
    expect(t).toContain("operator");
    expect(t).toContain("(Повтор)");
  });

  it("подписи мастера и специалиста по ОТ", () => {
    const t = text();
    expect(t).toContain("Мастер участка");
    expect(t).toContain("Специалист по ОТ");
  });

  it("в подвале номер акта и страницы", () => {
    const footer = actDocument(data()).footer as (page: number, pages: number) => unknown;
    const t = JSON.stringify(footer(1, 2));
    expect(t).toContain("Стр. 1 из 2");
    expect(t).toContain("Акт № evt-000042");
  });
});

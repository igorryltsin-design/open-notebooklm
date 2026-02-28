import { describe, expect, it } from "vitest";
import {
  UI_STATUS_STATES,
  mapStatusTone,
  buildDocumentStatus,
  buildSummaryStatus,
  buildScriptStatus,
} from "../uiStatus";

describe("uiStatus", () => {
  it("maps state to tone consistently", () => {
    expect(mapStatusTone(UI_STATUS_STATES.IDLE)).toBe("state-idle");
    expect(mapStatusTone(UI_STATUS_STATES.LOADING)).toBe("state-loading");
    expect(mapStatusTone(UI_STATUS_STATES.READY)).toBe("is-on");
    expect(mapStatusTone(UI_STATUS_STATES.ERROR)).toBe("state-error");
  });

  it("builds document status for idle/loading/ready/error", () => {
    const idle = buildDocumentStatus({});
    expect(idle.state).toBe("idle");
    expect(idle.detail).toContain("не выбран");

    const loading = buildDocumentStatus({
      documentId: "doc-1",
      filename: "Файл.pdf",
      autoIngesting: true,
    });
    expect(loading.state).toBe("loading");
    expect(loading.detail).toContain("автоиндексация");

    const ready = buildDocumentStatus({
      documentId: "doc-1",
      filename: "Файл.pdf",
      ingested: true,
      chunks: 17,
    });
    expect(ready.state).toBe("ready");
    expect(ready.detail).toContain("17 фрагм.");

    const error = buildDocumentStatus({
      documentId: "doc-1",
      filename: "Файл.pdf",
      error: "broken",
    });
    expect(error.state).toBe("error");
    expect(error.tone).toBe("state-error");
  });

  it("builds summary status with quantitative detail", () => {
    const loading = buildSummaryStatus({
      streamingSummary: "abc",
      chars: 3,
    });
    expect(loading.state).toBe("loading");
    expect(loading.detail).toBe("генерация · 3 симв.");

    const ready = buildSummaryStatus({
      summary: "готовый текст",
      chars: 12,
      sourcesCount: 2,
    });
    expect(ready.state).toBe("ready");
    expect(ready.detail).toBe("готово · 12 симв., 2 ист.");

    const readyZero = buildSummaryStatus({
      isReady: true,
      chars: 0,
      sourcesCount: 0,
    });
    expect(readyZero.detail).toBe("готово · 0 симв., 0 ист.");
  });

  it("builds script status with quantitative detail", () => {
    const loading = buildScriptStatus({
      streamingScript: "abc",
      chars: 3,
    });
    expect(loading.state).toBe("loading");
    expect(loading.detail).toBe("генерация · 3 симв.");

    const ready = buildScriptStatus({
      script: [{ voice: "A", text: "x" }, { voice: "B", text: "y" }],
      lines: 2,
    });
    expect(ready.state).toBe("ready");
    expect(ready.detail).toBe("готово · 2 реплик");
  });
});

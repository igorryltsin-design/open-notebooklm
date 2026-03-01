import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import ScriptPanel from "../ScriptPanel";

const api = {
  importScript: vi.fn(),
  getScriptTtsQuality: vi.fn(),
  getScriptLocks: vi.fn(),
  saveScriptLocks: vi.fn(),
  previewScriptLine: vi.fn(),
  regenerateScriptLine: vi.fn(),
  downloadUrl: vi.fn((x) => `/api/download/${x}`),
  getScriptTimeline: vi.fn(),
  downloadScriptExport: vi.fn(),
  getScriptMetrics: vi.fn(),
  getScriptVersions: vi.fn(),
  compareScriptVersions: vi.fn(),
  restoreScriptVersion: vi.fn(),
  downloadDocumentBundle: vi.fn(),
  downloadReportDocx: vi.fn(),
};

vi.mock("../../api/client", () => ({
  importScript: (...args) => api.importScript(...args),
  getScriptTtsQuality: (...args) => api.getScriptTtsQuality(...args),
  getScriptLocks: (...args) => api.getScriptLocks(...args),
  saveScriptLocks: (...args) => api.saveScriptLocks(...args),
  previewScriptLine: (...args) => api.previewScriptLine(...args),
  regenerateScriptLine: (...args) => api.regenerateScriptLine(...args),
  downloadUrl: (...args) => api.downloadUrl(...args),
  getScriptTimeline: (...args) => api.getScriptTimeline(...args),
  downloadScriptExport: (...args) => api.downloadScriptExport(...args),
  getScriptMetrics: (...args) => api.getScriptMetrics(...args),
  getScriptVersions: (...args) => api.getScriptVersions(...args),
  compareScriptVersions: (...args) => api.compareScriptVersions(...args),
  restoreScriptVersion: (...args) => api.restoreScriptVersion(...args),
  downloadDocumentBundle: (...args) => api.downloadDocumentBundle(...args),
  downloadReportDocx: (...args) => api.downloadReportDocx(...args),
}));

describe("ScriptPanel smoke", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    try {
      sessionStorage.clear();
    } catch (_) {}
    api.getScriptTimeline.mockResolvedValue({ chapters: [] });
    api.getScriptMetrics.mockResolvedValue({});
    api.getScriptVersions.mockResolvedValue({
      current_version_id: "v-cur",
      versions: [{ version_id: "v-cur", label: "v1", reason: "import", line_count: 1, created_at: "2026-01-01T00:00:00Z", is_current: true }],
    });
    api.compareScriptVersions.mockResolvedValue({ diff: { changed: 0, added: 0, removed: 0, changes: [] } });
    api.restoreScriptVersion.mockResolvedValue({ script: [] });
    api.getScriptLocks.mockResolvedValue({ locks: [] });
    api.saveScriptLocks.mockResolvedValue({ locks: [] });
    api.getScriptTtsQuality.mockResolvedValue({
      totals: { errors: 0, warnings: 1, lines: 1 },
      lines: [
        {
          index: 0,
          suggestion:
            "Здравствуйте, уважаемые слушатели! Меня зовут Игорь, и я веду этот подкаст. Сегодня у нас интересная тема, мы поговорим о платформе, которая становится все более популярной среди команд. Вместе с экспертами мы разберем, для чего она нужна и как ее использовать. Итак, давайте начнем!",
          issues: [
            { code: "long_line", severity: "warn", message: "Слишком длинная реплика; лучше разбить на две." },
          ],
        },
      ],
    });
    api.importScript.mockImplementation(async (_documentId, nextScript) => ({ script: nextScript }));
  });

  it("applies tts normalization and splits long line", async () => {
    const onScriptImported = vi.fn();

    render(
      <ScriptPanel
        documentId="doc-1"
        script={[{ voice: "ИГОРЬ", text: "Очень длинная исходная реплика без нормализации" }]}
        streamingRaw=""
        onScriptImported={onScriptImported}
        onError={vi.fn()}
      />,
    );

    const applyBtn = await screen.findByRole("button", { name: /Применить нормализацию и разбить/i });
    fireEvent.click(applyBtn);

    await waitFor(() => expect(api.importScript).toHaveBeenCalledTimes(1));
    const [docId, nextScript] = api.importScript.mock.calls[0];
    expect(docId).toBe("doc-1");
    expect(Array.isArray(nextScript)).toBe(true);
    expect(nextScript).toHaveLength(2);
    expect(nextScript[0].voice).toBe("ИГОРЬ");
    expect(nextScript[1].voice).toBe("ИГОРЬ");
    expect(nextScript[0].text.length).toBeGreaterThan(20);
    expect(nextScript[1].text.length).toBeGreaterThan(20);
    await waitFor(() => expect(onScriptImported).toHaveBeenCalled());
  });

  it("shows badge for lines marked as external model knowledge", async () => {
    render(
      <ScriptPanel
        documentId="doc-grounding"
        script={[{ voice: "HOST", text: "Вне документа: стоит добавить пилотный запуск.", grounding: "hybrid_external" }]}
        streamingRaw=""
        onScriptImported={vi.fn()}
        onError={vi.fn()}
      />,
    );

    expect(await screen.findByText(/^вне документа$/i)).toBeInTheDocument();
  });

  it("does not crash when switching from streaming state to ready script", async () => {
    const view = render(
      <ScriptPanel
        documentId="doc-stream"
        script={[]}
        streamingRaw="<think>Промежуточное</think>Черновик"
        onScriptImported={vi.fn()}
        onError={vi.fn()}
      />,
    );

    expect(await screen.findByText(/Пишу скрипт/i)).toBeInTheDocument();

    view.rerender(
      <ScriptPanel
        documentId="doc-stream"
        script={[{ voice: "HOST", text: "Готовая реплика" }]}
        streamingRaw=""
        onScriptImported={vi.fn()}
        onError={vi.fn()}
      />,
    );

    expect(await screen.findByText("Готовая реплика")).toBeInTheDocument();
  });

  it("persists filters in session and shows empty filter state", async () => {
    const baseScript = [
      { voice: "HOST", text: "Первая реплика" },
      { voice: "GUEST", text: "Вторая реплика" },
    ];
    const view = render(
      <ScriptPanel
        documentId="doc-filters"
        script={baseScript}
        streamingRaw=""
        onScriptImported={vi.fn()}
        onError={vi.fn()}
      />,
    );

    const voiceSelect = await screen.findByTitle(/Фильтр по голосу/i);
    fireEvent.change(voiceSelect, { target: { value: "HOST" } });

    const searchInput = screen.getByPlaceholderText(/Поиск по репликам/i);
    fireEvent.change(searchInput, { target: { value: "нет такого текста" } });
    expect(screen.getByText(/По текущим фильтрам реплик не найдено/i)).toBeInTheDocument();

    view.unmount();

    render(
      <ScriptPanel
        documentId="doc-filters"
        script={baseScript}
        streamingRaw=""
        onScriptImported={vi.fn()}
        onError={vi.fn()}
      />,
    );

    expect(await screen.findByTitle(/Фильтр по голосу/i)).toHaveValue("HOST");
    expect(screen.getByPlaceholderText(/Поиск по репликам/i)).toHaveValue("нет такого текста");
  });
});

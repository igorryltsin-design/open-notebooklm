import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import ChatPanel from "../ChatPanel";

const api = {
  listDocuments: vi.fn(),
  compareDocuments: vi.fn(),
  queryChat: vi.fn(),
  consumeChatStream: vi.fn(),
  consumeConversationalChatStream: vi.fn(),
  getChatHistory: vi.fn(),
  clearChatHistory: vi.fn(),
  consumeVoiceQaStream: vi.fn(),
  downloadUrl: vi.fn((name) => `/api/download/${name}`),
  getDocumentChunk: vi.fn(),
  getDocumentFullText: vi.fn(),
  getDocumentSourceUrl: vi.fn((documentId) => `/api/documents/${documentId}/source`),
};

vi.mock("../../api/client", () => ({
  listDocuments: (...args) => api.listDocuments(...args),
  compareDocuments: (...args) => api.compareDocuments(...args),
  queryChat: (...args) => api.queryChat(...args),
  consumeChatStream: (...args) => api.consumeChatStream(...args),
  consumeConversationalChatStream: (...args) => api.consumeConversationalChatStream(...args),
  getChatHistory: (...args) => api.getChatHistory(...args),
  clearChatHistory: (...args) => api.clearChatHistory(...args),
  consumeVoiceQaStream: (...args) => api.consumeVoiceQaStream(...args),
  downloadUrl: (...args) => api.downloadUrl(...args),
  getDocumentChunk: (...args) => api.getDocumentChunk(...args),
  getDocumentFullText: (...args) => api.getDocumentFullText(...args),
  getDocumentSourceUrl: (...args) => api.getDocumentSourceUrl(...args),
}));

function createMemoryStorage() {
  const store = new Map();
  return {
    getItem: (k) => (store.has(String(k)) ? store.get(String(k)) : null),
    setItem: (k, v) => { store.set(String(k), String(v)); },
    removeItem: (k) => { store.delete(String(k)); },
    clear: () => { store.clear(); },
  };
}

describe("ChatPanel smoke", () => {
  let localStorageDescriptor;
  let sessionStorageDescriptor;

  beforeEach(() => {
    vi.clearAllMocks();
    localStorageDescriptor = Object.getOwnPropertyDescriptor(window, "localStorage");
    sessionStorageDescriptor = Object.getOwnPropertyDescriptor(window, "sessionStorage");
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      value: createMemoryStorage(),
    });
    Object.defineProperty(window, "sessionStorage", {
      configurable: true,
      value: createMemoryStorage(),
    });
    try {
      localStorage.removeItem("chat-question-mode");
    } catch (_) {}
    api.listDocuments.mockResolvedValue([
      { document_id: "doc-1", filename: "Документ 1.pdf" },
    ]);
    api.getChatHistory.mockResolvedValue({
      messages: [
        { role: "user", text: "Что важно?" },
        {
          role: "assistant",
          text: "Короткий ответ",
          citations: [
            {
              document_id: "doc-1",
              chunk_id: "c-7",
              chunk_index: 2,
              page: 12,
              section_path: "Глава 2 / Архитектура",
              text: "Фрагмент источника с важной деталью.",
              highlights: ["Важная деталь", "Ещё одна строка"],
              score: 0.91,
            },
          ],
        },
      ],
    });
    api.getDocumentChunk.mockResolvedValue({
      document_id: "doc-1",
      chunk_id: "c-7",
      chunk_index: 2,
      text: "Фрагмент источника с важной деталью и пояснением.",
      page: 12,
      section_path: "Глава 2 / Архитектура",
      source_locator: {
        kind: "pdf",
        page: 12,
        quote: "Важная деталь",
        file_extension: "pdf",
      },
      source_url: "/api/documents/doc-1/source",
    });
    api.getDocumentFullText.mockResolvedValue({ text: "" });
  });

  afterEach(() => {
    if (localStorageDescriptor) {
      Object.defineProperty(window, "localStorage", localStorageDescriptor);
    }
    if (sessionStorageDescriptor) {
      Object.defineProperty(window, "sessionStorage", sessionStorageDescriptor);
    }
  });

  it("renders compact controls and citation page/section preview", async () => {
    const view = render(
      <ChatPanel
        currentDocumentId="doc-1"
        externalSelectedDocumentIds={[]}
        activeProjectContext={null}
        onError={vi.fn()}
      />,
    );

    const showAdvancedBtn = await screen.findByRole("button", { name: /Показать расширенные настройки/i });
    fireEvent.click(showAdvancedBtn);

    const answerModeSelect = await screen.findByLabelText(/Режим ответа/i);
    expect(answerModeSelect).toHaveValue("default");
    expect(await screen.findByText("Короткий ответ")).toBeInTheDocument();

    fireEvent.change(answerModeSelect, { target: { value: "quote" } });
    expect(answerModeSelect).toHaveValue("quote");

    const chip = await waitFor(() => {
      const btn = screen
        .getAllByRole("button")
        .find((el) => (el.textContent || "").includes("doc-1/#2") && (el.textContent || "").includes("стр. 12"));
      expect(btn).toBeTruthy();
      return btn;
    });
    expect(chip).toBeInTheDocument();
    expect(chip.textContent || "").toContain("Архитектура");
    expect(chip.textContent || "").toContain("стр. 12");
    view.unmount();
  });

  it("does not open voice modal on Enter text send", async () => {
    let resolveQuery;
    api.getChatHistory.mockResolvedValueOnce({ messages: [] });
    api.queryChat.mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          resolveQuery = resolve;
        }),
    );

    const view = render(
      <ChatPanel
        currentDocumentId="doc-1"
        externalSelectedDocumentIds={[]}
        activeProjectContext={null}
        onError={vi.fn()}
      />,
    );

    const textarea = await screen.findByPlaceholderText(/Ваш вопрос по текущему документу/i);
    fireEvent.change(textarea, { target: { value: "Проверь по документу" } });
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter", charCode: 13 });

    await waitFor(() => {
      expect(api.queryChat).toHaveBeenCalledTimes(1);
    });
    expect(api.consumeVoiceQaStream).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog", { name: /Голосовой режим/i })).not.toBeInTheDocument();

    resolveQuery?.({
      answer: "Готово",
      citations: [],
      confidence: 0.8,
    });

    await screen.findByText("Готово");
    expect(screen.queryByRole("dialog", { name: /Голосовой режим/i })).not.toBeInTheDocument();
    view.unmount();
  });

  it("sends hybrid knowledge mode in QA and styles external answer block", async () => {
    api.getChatHistory.mockResolvedValueOnce({ messages: [] });
    api.queryChat.mockResolvedValueOnce({
      answer: "Вне документа: можно предложить дополнительный план внедрения.",
      confidence: 0.77,
      citations: [],
      has_model_knowledge_content: true,
    });

    const view = render(
      <ChatPanel
        currentDocumentId="doc-1"
        externalSelectedDocumentIds={[]}
        activeProjectContext={null}
        onError={vi.fn()}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: /Показать расширенные настройки/i }));
    fireEvent.change(screen.getByLabelText(/Опора ответа/i), { target: { value: "hybrid_model" } });
    fireEvent.change(screen.getByPlaceholderText(/Ваш вопрос по текущему документу/i), { target: { value: "Что можно улучшить?" } });
    fireEvent.click(screen.getByRole("button", { name: /Отправить/i }));

    await waitFor(() => expect(api.queryChat).toHaveBeenCalledTimes(1));
    expect(api.queryChat.mock.calls[0][0].knowledge_mode).toBe("hybrid_model");
    expect(await screen.findByText(/^Вне документа$/i)).toBeInTheDocument();
    expect(view.container.querySelector('.chat-external-knowledge-block')).toBeTruthy();
  });

  it("opens source viewer from citation preview", async () => {
    const view = render(
      <ChatPanel
        currentDocumentId="doc-1"
        externalSelectedDocumentIds={[]}
        activeProjectContext={null}
        onError={vi.fn()}
      />,
    );
    const chip = await waitFor(() => {
      const btn = screen
        .getAllByRole("button")
        .find((el) => (el.textContent || "").includes("doc-1/#2"));
      expect(btn).toBeTruthy();
      return btn;
    });
    fireEvent.click(chip);
    expect(await screen.findByRole("dialog", { name: /Просмотр документа/i })).toBeInTheDocument();
    expect(api.getDocumentChunk).toHaveBeenCalled();
    view.unmount();
  });

  it("clears lite source when citations belong to another document", async () => {
    api.getChatHistory.mockResolvedValueOnce({
      messages: [
        {
          role: "assistant",
          text: "Ответ из другого документа",
          citations: [
            {
              document_id: "doc-2",
              chunk_id: "c-99",
              chunk_index: 0,
              text: "Чужой фрагмент",
            },
          ],
        },
      ],
    });
    api.listDocuments.mockResolvedValueOnce([
      { document_id: "doc-1", filename: "Документ 1.pdf" },
      { document_id: "doc-2", filename: "Документ 2.pdf" },
    ]);

    const onSourceCitationOpen = vi.fn();
    const view = render(
      <ChatPanel
        currentDocumentId="doc-1"
        externalSelectedDocumentIds={[]}
        activeProjectContext={null}
        onError={vi.fn()}
        liteMode
        onSourceCitationOpen={onSourceCitationOpen}
      />,
    );

    await waitFor(() => {
      expect(onSourceCitationOpen).toHaveBeenCalled();
    });
    const lastCall = onSourceCitationOpen.mock.calls[onSourceCitationOpen.mock.calls.length - 1];
    expect(lastCall?.[0] ?? null).toBeNull();
    view.unmount();
  });

  it("question -> citation click -> highlighted source is visible", async () => {
    api.getChatHistory.mockResolvedValueOnce({ messages: [] });
    api.queryChat.mockResolvedValueOnce({
      answer: "Итог по документу",
      confidence: 0.82,
      citations: [
        {
          document_id: "doc-1",
          chunk_id: "c-42",
          chunk_index: 0,
          page: 3,
          text: "Ключевая формулировка должна быть подсвечена.",
          highlights: ["Ключевая формулировка"],
          source_locator: {
            kind: "pdf",
            page: 3,
            quote: "Ключевая формулировка",
            file_extension: "pdf",
          },
        },
      ],
    });
    api.getDocumentChunk.mockImplementation(async (documentId, chunkId) => ({
      document_id: documentId,
      chunk_id: chunkId,
      chunk_index: 0,
      page: 3,
      text: "Полный фрагмент: Ключевая формулировка должна быть подсвечена в просмотре.",
      source_locator: {
        kind: "pdf",
        page: 3,
        quote: "Ключевая формулировка",
        file_extension: "pdf",
      },
    }));
    api.getDocumentFullText.mockResolvedValueOnce({
      text: "Большой текст документа. Ключевая формулировка находится в этом абзаце.",
    });

    const view = render(
      <ChatPanel
        currentDocumentId="doc-1"
        externalSelectedDocumentIds={[]}
        activeProjectContext={null}
        onError={vi.fn()}
      />,
    );

    const textarea = await screen.findByPlaceholderText(/Ваш вопрос по текущему документу/i);
    fireEvent.change(textarea, { target: { value: "Что важно?" } });
    fireEvent.keyDown(textarea, { key: "Enter", code: "Enter", charCode: 13 });

    expect(await screen.findByText("Итог по документу")).toBeInTheDocument();

    const chip = await waitFor(() => {
      const btn = screen
        .getAllByRole("button")
        .find((el) => (el.textContent || "").includes("doc-1/#0"));
      expect(btn).toBeTruthy();
      return btn;
    });
    fireEvent.click(chip);

    const dialog = await screen.findByRole("dialog", { name: /Просмотр документа/i });
    const marked = await waitFor(() => dialog.querySelector("mark"));
    expect(marked).toBeTruthy();
    expect((marked?.textContent || "").toLowerCase()).toContain("ключевая формулировка");
    view.unmount();
  });
});

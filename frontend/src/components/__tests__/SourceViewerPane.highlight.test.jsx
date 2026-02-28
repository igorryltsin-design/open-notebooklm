import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import SourceViewerPane from "../SourceViewerPane";

const api = {
  getDocumentChunk: vi.fn(),
  getDocumentFullText: vi.fn(),
  getDocumentSourceUrl: vi.fn((documentId) => `/api/documents/${documentId}/source`),
};

vi.mock("../../api/client", () => ({
  getDocumentChunk: (...args) => api.getDocumentChunk(...args),
  getDocumentFullText: (...args) => api.getDocumentFullText(...args),
  getDocumentSourceUrl: (...args) => api.getDocumentSourceUrl(...args),
}));

describe("SourceViewerPane highlight", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    api.getDocumentFullText.mockResolvedValue({ text: "" });
  });

  it("prefers offset-based highlight when locator contains char range", async () => {
    const rowText = "Пояснение. ВАЖНЫЙ ФРАГМЕНТ для проверки подсветки.";
    const start = rowText.indexOf("ВАЖНЫЙ ФРАГМЕНТ");
    const end = start + "ВАЖНЫЙ ФРАГМЕНТ".length;

    api.getDocumentChunk.mockResolvedValue({
      document_id: "doc-1",
      chunk_id: "c-1",
      chunk_index: 0,
      text: rowText,
      source_locator: {
        kind: "text",
        char_start: start,
        char_end: end,
        quote: "ВАЖНЫЙ ФРАГМЕНТ",
      },
    });

    render(
      <SourceViewerPane
        citation={{
          document_id: "doc-1",
          chunk_id: "c-1",
          chunk_index: 0,
          text: rowText,
          highlights: ["ВАЖНЫЙ ФРАГМЕНТ"],
          source_locator: { quote: "ВАЖНЫЙ ФРАГМЕНТ" },
        }}
        documentId="doc-1"
        onError={vi.fn()}
      />,
    );

    await waitFor(() => {
      const node = screen.getByText("ВАЖНЫЙ ФРАГМЕНТ");
      expect(node.tagName.toLowerCase()).toBe("mark");
    });
    expect(api.getDocumentFullText).toHaveBeenCalledWith(
      "doc-1",
      expect.objectContaining({
        maxChars: 90000,
        highlight: "ВАЖНЫЙ ФРАГМЕНТ",
      }),
    );
  });

  it("falls back to text search highlight when offset is invalid", async () => {
    const rowText = "Это длинный текст, где есть уникальная формулировка для поиска.";
    api.getDocumentChunk.mockResolvedValue({
      document_id: "doc-1",
      chunk_id: "c-2",
      chunk_index: 1,
      text: rowText,
      source_locator: {
        kind: "text",
        char_start: 9999,
        char_end: 10010,
        quote: "уникальная формулировка",
      },
    });

    render(
      <SourceViewerPane
        citation={{
          document_id: "doc-1",
          chunk_id: "c-2",
          chunk_index: 1,
          text: rowText,
          highlights: ["уникальная формулировка"],
          source_locator: { quote: "уникальная формулировка" },
        }}
        documentId="doc-1"
        onError={vi.fn()}
      />,
    );

    const marked = await waitFor(() => {
      const node = screen.getByText(/уникальная формулировка/i);
      expect(node.tagName.toLowerCase()).toBe("mark");
      return node;
    });
    expect(marked).toBeInTheDocument();
  });

  it("switches between text and document modes in PDF source viewer", async () => {
    api.getDocumentChunk.mockResolvedValue({
      document_id: "doc-pdf",
      chunk_id: "c-3",
      chunk_index: 2,
      text: "PDF chunk preview text.",
      page: 4,
      source_locator: {
        kind: "pdf",
        page: 4,
        file_extension: "pdf",
        quote: "preview",
      },
    });
    api.getDocumentFullText.mockResolvedValueOnce({ text: "PDF text fallback" });

    render(
      <SourceViewerPane
        citation={{
          document_id: "doc-pdf",
          chunk_id: "c-3",
          chunk_index: 2,
          text: "PDF chunk preview text.",
          source_locator: { kind: "pdf", page: 4, file_extension: "pdf", quote: "preview" },
        }}
        documentId="doc-pdf"
        onError={vi.fn()}
      />,
    );

    const docBtn = await screen.findByRole("button", { name: "Документ" });
    expect(docBtn).toBeInTheDocument();
    fireEvent.click(docBtn);

    await waitFor(() => {
      expect(screen.getByTitle("Оригинальный документ")).toBeInTheDocument();
    });
  });

  it("loads full text when switching a whole PDF document from document mode to text mode", async () => {
    api.getDocumentFullText.mockResolvedValue({ text: "PDF full document text." });

    render(
      <SourceViewerPane
        citation={null}
        documentId="doc-pdf-full"
        filename="manual.pdf"
        onError={vi.fn()}
      />,
    );

    const docBtn = await screen.findByRole("button", { name: "Документ" });
    expect(docBtn).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Текст" }));

    await waitFor(() => {
      expect(api.getDocumentFullText).toHaveBeenCalled();
    });

    await screen.findByText((_, node) => node?.textContent === "PDF full document text.");
  });

  it("uses windowed rendering for large full text and keeps highlight visible", async () => {
    const quote = "ЦЕЛЕВОЙ ФРАГМЕНТ ДЛЯ ОКНА";
    const prefix = "Вводный текст.\n".repeat(12000);
    const suffix = "\nЗаключение.".repeat(7000);
    const fullText = `${prefix}${quote}${suffix}`;
    const start = fullText.indexOf(quote);
    const end = start + quote.length;

    api.getDocumentChunk.mockResolvedValue({
      document_id: "doc-large",
      chunk_id: "c-large",
      chunk_index: 0,
      text: quote,
      source_locator: {
        kind: "text",
        char_start: start,
        char_end: end,
        quote,
      },
    });
    api.getDocumentFullText.mockResolvedValueOnce({ text: fullText });

    render(
      <SourceViewerPane
        citation={{
          document_id: "doc-large",
          chunk_id: "c-large",
          chunk_index: 0,
          text: quote,
          highlights: [quote],
          source_locator: { quote },
        }}
        documentId="doc-large"
        onError={vi.fn()}
      />,
    );

    await screen.findByRole("button", { name: "Показать весь текст" });
    expect(screen.getByText(/начало документа скрыто/i)).toBeInTheDocument();

    const marked = await waitFor(() => {
      const node = screen.getByText(quote);
      expect(node.tagName.toLowerCase()).toBe("mark");
      return node;
    });
    expect(marked).toBeInTheDocument();
  });

  it("does not stay on chunk-loading skeleton when full text is already available", async () => {
    const chunkNeverResolves = new Promise(() => {});
    const fullText = "Полный текст документа с нужной цитатой для отображения.";

    api.getDocumentChunk.mockReturnValueOnce(chunkNeverResolves);
    api.getDocumentFullText.mockResolvedValueOnce({ text: fullText });

    render(
      <SourceViewerPane
        citation={{
          document_id: "doc-pending",
          chunk_id: "c-pending",
          chunk_index: 0,
          text: "нужной цитатой",
          highlights: ["нужной цитатой"],
          source_locator: { quote: "нужной цитатой" },
        }}
        documentId="doc-pending"
        onError={vi.fn()}
      />,
    );

    await screen.findByText(/показываем извлечённый текст документа/i);
    expect(screen.getByText(/для отображения\./i)).toBeInTheDocument();
    expect(screen.queryByText(/Загрузка фрагмента/i)).not.toBeInTheDocument();
  });
});

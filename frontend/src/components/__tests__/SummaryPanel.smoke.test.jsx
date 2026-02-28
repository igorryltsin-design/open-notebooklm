import React from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi, beforeEach } from "vitest";
import SummaryPanel from "../SummaryPanel";

describe("SummaryPanel smoke", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
  });

  it("renders summary, copies text, and toggles sources", async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(globalThis.navigator, { clipboard: { writeText } });

    render(
      <SummaryPanel
        summary={"# Заголовок\n\nТекст ответа\n<think>скрытое рассуждение</think>"}
        isStreaming={false}
        sources={[{ chunk_id: "c1", text: "Фрагмент источника" }]}
      />,
    );

    expect(screen.getByRole("heading", { name: /саммари/i })).toBeInTheDocument();
    expect(screen.getByText("Заголовок")).toBeInTheDocument();
    expect(screen.getByText("Рассуждение модели")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /копировать саммари/i }));
    expect(writeText).toHaveBeenCalledWith(expect.stringContaining("Текст ответа"));
    expect(writeText).not.toHaveBeenCalledWith(expect.stringContaining("скрытое рассуждение"));

    fireEvent.click(screen.getByRole("button", { name: /показать источники/i }));
    expect(screen.getByText("Фрагмент источника")).toBeInTheDocument();
  });

  it("shows explicit empty and loading states before ready content", () => {
    const { rerender } = render(<SummaryPanel summary="" isStreaming={false} sources={[]} />);
    expect(screen.getByText(/Саммари пока не создано/i)).toBeInTheDocument();

    rerender(<SummaryPanel summary="" isStreaming sources={[]} />);
    expect(screen.getByText(/Начинаю собирать саммари/i)).toBeInTheDocument();
  });

  it("deduplicates repeated source rows", () => {
    const duplicated = [
      { document_id: "doc-1", chunk_id: "c-1", anchor_id: "a-1", text: "Первый фрагмент" },
      { document_id: "doc-1", chunk_id: "c-1", anchor_id: "a-1", text: "Первый фрагмент" },
      { document_id: "doc-1", chunk_id: "c-2", anchor_id: "a-2", text: "Второй фрагмент" },
    ];
    render(
      <SummaryPanel
        summary={"Итоговый текст"}
        isStreaming={false}
        sources={duplicated}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Показать источники/i }));
    const rows = screen.getAllByRole("listitem");
    expect(rows).toHaveLength(2);
  });
});

import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import JobPanel from "../JobPanel";

const startPolling = vi.fn();
const mockUseJobPoller = vi.fn();
const cancelJob = vi.fn();
const retryJob = vi.fn();
const getDocumentArtifacts = vi.fn();

vi.mock("../../hooks/useJobPoller", () => ({
  useJobPoller: () => mockUseJobPoller(),
}));

vi.mock("../../api/client", () => ({
  cancelJob: (...args) => cancelJob(...args),
  retryJob: (...args) => retryJob(...args),
  getDocumentArtifacts: (...args) => getDocumentArtifacts(...args),
  downloadUrl: (filename) => `/api/download/${filename}`,
}));

describe("JobPanel smoke", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getDocumentArtifacts.mockResolvedValue({ files: [] });
    mockUseJobPoller.mockReturnValue({ job: null, startPolling, reset: vi.fn() });
  });

  it("shows queue/lane runtime metadata for pending job", async () => {
    mockUseJobPoller.mockReturnValue({
      job: {
        job_id: "j1",
        status: "pending",
        progress: 15,
        lane: "audio",
        lane_limit: 1,
        lane_running: 1,
        lane_pending: 3,
        queue_position: 2,
        output_paths: [],
      },
      startPolling,
      reset: vi.fn(),
    });

    render(<JobPanel jobId="j1" label="Генерация аудио" documentId="doc1" artifactKind="audio" />);

    expect(await screen.findByText(/Очередь audio/i)).toBeInTheDocument();
    expect(screen.getByText(/выполняется 1\/1/i)).toBeInTheDocument();
    expect(screen.getByText(/ожидает 3/i)).toBeInTheDocument();
    expect(screen.getByText(/Позиция в очереди: #2/i)).toBeInTheDocument();
    expect(startPolling).toHaveBeenCalledWith("j1");
  });

  it("calls cancel and retry actions", async () => {
    cancelJob.mockResolvedValue({ ok: true });
    retryJob.mockResolvedValue({ job_id: "j2" });

    const { rerender } = render(<JobPanel jobId="j1" label="Задача" />);

    mockUseJobPoller.mockReturnValue({
      job: {
        job_id: "j1",
        status: "running",
        progress: 40,
        job_type: "audio",
        output_paths: [],
      },
      startPolling,
      reset: vi.fn(),
    });
    rerender(<JobPanel jobId="j1" label="Задача" />);

    fireEvent.click(await screen.findByRole("button", { name: "Отменить" }));
    await waitFor(() => expect(cancelJob).toHaveBeenCalledWith("j1"));
    expect(startPolling).toHaveBeenCalledWith("j1");

    mockUseJobPoller.mockReturnValue({
      job: {
        job_id: "j1",
        status: "error",
        progress: 40,
        job_type: "audio",
        output_paths: [],
      },
      startPolling,
      reset: vi.fn(),
    });
    rerender(<JobPanel jobId="j1" label="Задача" />);

    fireEvent.click(await screen.findByRole("button", { name: "Повторить" }));
    await waitFor(() => expect(retryJob).toHaveBeenCalledWith("j1"));
    expect(startPolling).toHaveBeenCalledWith("j2");
  });
});

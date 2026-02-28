import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { getDocumentChunk, getDocumentFullText, uploadFile } from "../client";

describe("api client timeouts", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    vi.spyOn(globalThis, "fetch").mockImplementation((_, options = {}) => {
      return new Promise((resolve, reject) => {
        const signal = options?.signal;
        if (signal?.aborted) {
          const err = new Error("aborted");
          err.name = "AbortError";
          reject(err);
          return;
        }
        signal?.addEventListener("abort", () => {
          const err = new Error("aborted");
          err.name = "AbortError";
          reject(err);
        });
        // never resolve: simulate hanging network
        void resolve;
      });
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it("aborts hanging chunk request by timeout", async () => {
    const promise = getDocumentChunk("doc-a", "chunk-a");
    const rejection = expect(promise).rejects.toMatchObject({
      code: "REQUEST_TIMEOUT",
    });
    await vi.advanceTimersByTimeAsync(12050);
    await rejection;
  });

  it("aborts hanging fulltext request by timeout", async () => {
    const promise = getDocumentFullText("doc-a");
    const rejection = expect(promise).rejects.toMatchObject({
      code: "REQUEST_TIMEOUT",
    });
    await vi.advanceTimersByTimeAsync(20050);
    await rejection;
  });

  it("extracts readable message from nginx html 413 response", async () => {
    globalThis.fetch.mockResolvedValueOnce(
      new Response(
        "<html><head><title>413 Request Entity Too Large</title></head><body><center><h1>413 Request Entity Too Large</h1></center></body></html>",
        {
          status: 413,
          headers: { "Content-Type": "text/html" },
        },
      ),
    );

    const file = new File(["payload"], "big.pdf", { type: "application/pdf" });
    await expect(uploadFile(file)).rejects.toMatchObject({
      message: "Файл больше 150 MB.",
      status: 413,
    });
  });
});

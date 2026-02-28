import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import App from "../App";

const api = {
  addProjectPin: vi.fn(),
  deleteDocument: vi.fn(),
  getRoleLlmSettings: vi.fn(),
  ingest: vi.fn(),
};

vi.mock("../api/client", () => ({
  addProjectPin: (...args) => api.addProjectPin(...args),
  deleteDocument: (...args) => api.deleteDocument(...args),
  getRoleLlmSettings: (...args) => api.getRoleLlmSettings(...args),
  ingest: (...args) => api.ingest(...args),
}));

vi.mock("../components/UploadPanel", () => ({
  default: ({ onUploaded }) => (
    <div>
      <button type="button" onClick={() => onUploaded?.("doc-2", "beta.docx")}>upload-doc-2</button>
    </div>
  ),
}));

vi.mock("../components/DocumentList", () => {
  const doc1 = {
    document_id: "doc-1",
    filename: "alpha.pdf",
    ingested: true,
    chunks: 3,
    summary: "11111111111111111111",
    sources: [{ document_id: "doc-1", chunk_id: "c-1", anchor_id: "a-1", text: "src" }],
    script: [{ voice: "A", text: "line1" }, { voice: "B", text: "line2" }],
  };
  return {
    default: ({ onOpen }) => (
      <div>
        <button type="button" onClick={() => onOpen(doc1)}>open-doc-1</button>
      </div>
    ),
  };
});

vi.mock("../components/ActionBar", () => ({
  default: ({ onScenarioRolesChange }) => (
    <div>
      <div data-testid="actionbar-mock" />
      <button type="button" onClick={() => onScenarioRolesChange?.(["moderator", "critic", "builder"])}>
        apply-scenario-roles
      </button>
    </div>
  ),
}));

vi.mock("../components/SummaryPanel", () => ({
  default: () => <div data-testid="summary-mock" />,
}));

vi.mock("../components/ScriptPanel", () => ({
  default: () => <div data-testid="script-mock" />,
}));

vi.mock("../components/JobPanel", () => ({
  default: () => <div data-testid="jobpanel-mock" />,
}));

vi.mock("../components/SettingsPanel", () => ({
  default: ({ participants }) => (
    <div data-testid="settings-mock">
      {Array.isArray(participants) ? participants.map((p) => `${p.role}:${p.name}`).join("|") : ""}
    </div>
  ),
}));

vi.mock("../components/ChatPanel", () => ({
  default: ({ currentDocumentId, onSourceCitationOpen }) => (
    <div>
      <button
        type="button"
        onClick={() => onSourceCitationOpen?.({
          document_id: currentDocumentId || "doc-1",
          chunk_id: "c-1",
          chunk_index: 0,
          anchor_id: "a-1",
        })}
      >
        open-citation
      </button>
    </div>
  ),
}));

vi.mock("../components/SourceViewerPane", () => ({
  default: ({ citation, documentId }) => (
    <div data-testid="source-pane-state">
      {`${String(documentId || "none")}:${String(citation?.chunk_id || "full")}`}
    </div>
  ),
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

describe("App integration", () => {
  let localStorageDescriptor;
  let sessionStorageDescriptor;

  beforeEach(() => {
    vi.clearAllMocks();
    api.getRoleLlmSettings.mockResolvedValue({ role_llm_map: {} });
    api.ingest.mockResolvedValue({ chunks: 3 });
    api.addProjectPin.mockResolvedValue({ ok: true });
    api.deleteDocument.mockResolvedValue({ ok: true });
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
  });

  afterEach(() => {
    if (localStorageDescriptor) {
      Object.defineProperty(window, "localStorage", localStorageDescriptor);
    }
    if (sessionStorageDescriptor) {
      Object.defineProperty(window, "sessionStorage", sessionStorageDescriptor);
    }
  });

  it("updates topbar statuses and resets source on document switch", async () => {
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: /Полный режим/i }));

    fireEvent.click(await screen.findByRole("button", { name: "open-doc-1" }));
    expect(await screen.findByTestId("source-pane-state")).toHaveTextContent("doc-1:full");
    expect(screen.getAllByText(/alpha\.pdf · индекс 3 фрагм\./i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/готово · 20 симв\., 1 ист\./i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getAllByText(/готово · 2 реплик/i).length).toBeGreaterThanOrEqual(1);

    fireEvent.click(screen.getByRole("button", { name: "open-citation" }));
    await waitFor(() => expect(screen.getByTestId("source-pane-state")).toHaveTextContent("doc-1:c-1"));

    fireEvent.click(screen.getByRole("button", { name: "upload-doc-2" }));
    await waitFor(() => expect(screen.getByTestId("source-pane-state")).toHaveTextContent("doc-2:full"));
  });

  it("syncs scenario roles into global participants settings", async () => {
    render(<App />);

    fireEvent.click(screen.getByRole("button", { name: /Полный режим/i }));
    fireEvent.click(await screen.findByRole("button", { name: "open-doc-1" }));
    fireEvent.click(screen.getByRole("button", { name: /Настройки/i }));

    expect(await screen.findByTestId("settings-mock")).toHaveTextContent("host:Игорь");

    fireEvent.click(screen.getByRole("button", { name: "apply-scenario-roles" }));

    await waitFor(() =>
      expect(screen.getByTestId("settings-mock")).toHaveTextContent("moderator:Игорь|critic:Аня|builder:Максим"),
    );
  });
});

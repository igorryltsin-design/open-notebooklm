import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
});

if (!globalThis.navigator) {
  globalThis.navigator = {};
}

if (!globalThis.navigator.clipboard) {
  globalThis.navigator.clipboard = {
    writeText: async () => {},
  };
}

if (!globalThis.Audio) {
  globalThis.Audio = class {
    play() {
      return Promise.resolve();
    }
    pause() {}
  };
}

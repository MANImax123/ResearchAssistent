import "@testing-library/jest-dom/vitest";
import { cleanup } from "@testing-library/react";
import { afterEach } from "vitest";

afterEach(() => {
  cleanup();
  localStorage.clear();
});

// jsdom has no layout; ProseMirror (TipTap) measures the cursor when scrolling it into view
const emptyRects = () => Object.assign([], { item: () => null }) as unknown as DOMRectList;
const emptyRect = () => new DOMRect(0, 0, 0, 0);
for (const proto of [Element.prototype, Range.prototype]) {
  if (!("getClientRects" in proto) || typeof proto.getClientRects !== "function") {
    Object.defineProperty(proto, "getClientRects", { value: emptyRects, configurable: true });
  }
  if (!("getBoundingClientRect" in proto) || typeof proto.getBoundingClientRect !== "function") {
    Object.defineProperty(proto, "getBoundingClientRect", { value: emptyRect, configurable: true });
  }
}
if (!document.elementFromPoint) document.elementFromPoint = () => null;

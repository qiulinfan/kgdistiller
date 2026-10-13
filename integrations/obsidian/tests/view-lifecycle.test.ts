import { describe, expect, it, vi } from "vitest";

vi.mock("obsidian", () => {
  class Component {}
  class ItemView extends Component {}
  return { Component, ItemView, MarkdownRenderer: {}, Notice: class {}, TFile: class {}, normalizePath: (path: string) => path, setIcon: () => undefined };
});

import { KgdistillerLookupView } from "../src/lookup-view";
import { KgdistillerGraphView } from "../src/view";

/**
 * Obsidian calls these on every view while mounting and closing it. They are
 * missing from the public typings, so TypeScript cannot catch a view method
 * that replaces one; a lookup method once named `open` left the view unmounted.
 */
const INTERNAL_VIEW_METHODS = ["open", "close", "load", "unload"];

describe("views keep Obsidian's internal lifecycle", () => {
  it.each([["lookup", KgdistillerLookupView], ["graph", KgdistillerGraphView]] as const)("the %s view defines none of its methods", (_name, view) => {
    const methods = Object.getOwnPropertyNames(view.prototype);
    for (const name of INTERNAL_VIEW_METHODS) expect(methods).not.toContain(name);
  });
});

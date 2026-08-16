import { describe, expect, it } from "vitest";
import { assertIndexReferences, assertOffline } from "../scripts/package-bundle.mjs";

const bytes = (value: string): Uint8Array => new TextEncoder().encode(value);

describe("closed frontend bundle inputs", () => {
  it.each([
    "https:evil.example/x",
    "http:\\\\evil.example\\x",
    "wss:evil.example/socket",
    "ftp:evil.example/file",
    "url(//evil.example/style.css)",
    "https:\\u002f\\u002fevil.example/x",
    '"http://www.w3.org/2000/svg.evil.example/x"',
    'fetch("//例子.测试/x")',
    'fetch("//[::1]/x")',
    'fetch("//%65vil.example/x")',
    'fetch("\\\\\\\\evil.example\\x")'
  ])("rejects remote special URL form %s", (hostile) => {
    expect(() => assertOffline("assets/app-deadbeef.js", bytes(hostile))).toThrow(/remote URL/u);
  });

  it("allows the exact SVG namespace without treating it as a dependency", () => {
    expect(() => assertOffline("assets/app-deadbeef.js", bytes('createElementNS("http://www.w3.org/2000/svg", "svg")'))).not.toThrow();
  });

  it("rejects an index reference missing from the exact build inventory", () => {
    const index = '<script type="module" src="/assets/app-deadbeef.js"></script><link rel="stylesheet" href="/assets/missing-deadbeef.css">';
    expect(() => assertIndexReferences(index, ["index.html", "assets/app-deadbeef.js"])).toThrow(/unavailable packaged asset/u);
  });
});

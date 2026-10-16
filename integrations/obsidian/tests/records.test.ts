import { readFileSync } from "node:fs";

import { describe, expect, it } from "vitest";

import { entryId, entryPath, firstLine, fold } from "../src/records";

describe("knowledge paths", () => {
  it("reads an accepted record's id, and only an accepted record's", () => {
    expect(entryId(".knowledge/entries/measure.md")).toBe("measure");
    expect(entryPath("measure")).toBe(".knowledge/entries/measure.md");
    for (const other of [".knowledge/drafts/measure.md", ".knowledge/entries/sub/measure.md", "notes/measure.md", ".knowledge/entries/measure.txt"]) {
      expect(entryId(other)).toBeUndefined();
    }
  });


  it("parses the first line of a lines value", () => {
    expect(firstLine("3-7")).toBe(3);
    expect(firstLine("9")).toBe(9);
    expect(firstLine("225-227, 5025-5043")).toBe(225);
    for (const bad of ["0", "7-3", "a", "", "3-", "3-7,", "5-7, 3-4", "3-5, 5-6", undefined]) {
      expect(firstLine(bad)).toBeUndefined();
    }
  });
});

describe("case folding parity with kgd", () => {
  it("folds as Python casefold does for the full foldings", () => {
    expect(fold("Straße GROẞ ΣΑΣ ς ＡＢ ﬁ")).toBe("strasse gross σασ σ ab fi");
    expect(fold("ᾳ ΐ ǰ ẖ ꭰ Ꭰ")).toBe("αι ΐ ǰ ẖ Ꭰ Ꭰ");
  });

  it("applies every entry of the case-folding table", () => {
    const table = JSON.parse(
      readFileSync(new URL("../src/case-folding.json", import.meta.url), "utf8"),
    ) as Record<string, string>;
    const stable = Object.entries(table).filter(([char]) => char.normalize("NFKC") === char);
    expect(stable.length).toBeGreaterThan(200);
    for (const [char, folded] of stable) expect(fold(char)).toBe(folded);
  });
});

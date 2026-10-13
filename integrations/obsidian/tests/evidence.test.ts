import { describe, expect, it } from "vitest";

import { closestSpan, mergeRanges, normalizeSpace, parseRanges, placeQuote, rangesText } from "../src/evidence";

const source = [
  "# Title",
  "",
  "Masked autoencoder（MAE）是一种简单 autoencoding approach：给定 signal 的部分 observation，重建原始 signal。",
  "",
  "令人意外的是，即使没有 data augmentation，MAE 也有不错表现 \\[[62](#^reference-62)\\]。",
  "简单的 random sampling 对 MAE 最好。",
];

describe("line ranges", () => {
  it("parse kgd's lines syntax and refuse malformed ranges", () => {
    expect(parseRanges("12")).toEqual([[12, 12]]);
    expect(parseRanges(" 12 - 14, 20 ")).toEqual([[12, 14], [20, 20]]);
    for (const bad of ["", "a", "5-3", "0", "1,,2", "3-"]) expect(parseRanges(bad)).toBeUndefined();
  });

  it("merge overlapping and adjacent ranges and print them as kgd does", () => {
    expect(mergeRanges([[20, 22], [5, 5], [6, 8], [21, 30]])).toEqual([[5, 8], [20, 30]]);
    expect(rangesText([[5, 8], [20, 20]])).toBe("5-8, 20");
  });

  it("collapse whitespace like kgd's normalize_space", () => {
    expect(normalizeSpace("  a \n\t b  ")).toBe("a b");
  });
});

describe("placing a quote", () => {
  it("keeps a quote that is verbatim in the cited lines, whitespace aside", () => {
    expect(placeQuote("即使没有  data\naugmentation，MAE 也有不错表现", [5, 5], source))
      .toEqual({ status: "verbatim", quote: "即使没有 data augmentation，MAE 也有不错表现", range: [5, 5] });
  });

  it("moves the range to the quote's only occurrence elsewhere", () => {
    expect(placeQuote("简单的 random sampling 对 MAE 最好", [3, 3], source))
      .toEqual({ status: "moved", quote: "简单的 random sampling 对 MAE 最好", range: [6, 6] });
  });

  it("snaps a near-verbatim quote to the source's own text inside the cited lines", () => {
    const placed = placeQuote("即使没有 data augmentation，MAE 也有很不错表现", [5, 5], source);
    expect(placed).toEqual({ status: "snapped", quote: "即使没有 data augmentation，MAE 也有不错表现", range: [5, 5] });
  });

  it("refuses a quote the source does not hold, or lines past its end", () => {
    expect(placeQuote("MAE 需要大量 data augmentation 才能工作", [5, 5], source).status).toBe("missing");
    expect(placeQuote("anything", [9, 9], source)).toEqual({ status: "missing", reason: "the source has only 6 lines" });
    expect(placeQuote("  ", [5, 5], source)).toEqual({ status: "missing", reason: "the quote is empty" });
  });

  it("does not move a quote that occurs more than once", () => {
    const twice = ["MAE 最好。", "", "MAE 最好。", "other"];
    expect(placeQuote("MAE 最好", [4, 4], twice).status).not.toBe("moved");
  });
});

describe("the closest span", () => {
  it("finds the best-aligned substring and its similarity across lines", () => {
    const span = closestSpan("autoencoding approach：给定 signal", source, [3, 5]);
    expect(span).toMatchObject({ text: "autoencoding approach：给定 signal", similarity: 1, range: [3, 3] });
  });

  it("reports the lines a span crosses", () => {
    const lines = ["first line ends here", "second line starts"];
    expect(closestSpan("ends here second line", lines, [1, 2])).toMatchObject({ similarity: 1, range: [1, 2] });
  });
});

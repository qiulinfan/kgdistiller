import type { FrontmatterRecord } from "../src/graph-model";

const entry = (id: string, frontmatter: Record<string, unknown>): FrontmatterRecord =>
  ({ path: `.knowledge/entries/${id}.md`, frontmatter });
const draft = (id: string, frontmatter: Record<string, unknown>): FrontmatterRecord =>
  ({ path: `.knowledge/drafts/${id}.md`, frontmatter });
const SOURCE = { source: "notes/chapter.tex", lines: "3-7" };

/**
 * Frontmatter as Obsidian's metadata cache reports it for a small base. Each
 * record exercises one rendering case named in its comment.
 */
export function recordFixture(): FrontmatterRecord[] {
  return [
    // nodes; a requires link and a pending term
    entry("sigma-algebra", { label: "Sigma algebra", kind: "definition", ...SOURCE, understanding: "understood", tags: ["math"] }),
    entry("measure", {
      label: "Measure", kind: "definition", ...SOURCE, aliases: ["测度"],
      understanding: "not-yet-understood", requires: ["[[sigma-algebra]]", "measurable space"],
    }),
    entry("measure-space", { label: "Measure space", kind: "definition", ...SOURCE, lines: 9 }),
    // binary: two link values in two roles, directed in frontmatter order
    entry("sigma-algebra-prerequisite-for-measure", {
      label: "Sigma algebra prerequisite for measure", kind: "prerequisite-for", ...SOURCE,
      prerequisite: ["[[sigma-algebra]]"], dependent: ["[[measure]]"],
    }),
    // self-relation: the same record twice in one role
    entry("measure-equals-itself", {
      label: "Measure equals itself", kind: "equivalent", ...SOURCE, side: ["[[measure]]", "[[Measure|the measure]]"],
    }),
    // n-ary: three link values, a diamond
    entry("measure-space-triple", {
      label: "Measure space triple", kind: "composes", ...SOURCE,
      whole: ["[[measure-space]]"], part: ["[[.knowledge/entries/sigma-algebra.md]]", "[[measure]]"],
    }),
    // relation about relations; its participants become diamonds
    entry("triple-contrasts-prerequisite", {
      label: "Triple contrasts prerequisite", kind: "contrasts", ...SOURCE,
      subject: ["[[measure-space-triple]]"], contrast: ["[[sigma-algebra-prerequisite-for-measure]]"],
    }),
    // foreign participant and a pending term
    entry("kl-divergence-example", {
      label: "KL divergence example", kind: "example", ...SOURCE,
      uses: ["[[notes:kl-divergence]]", "[[measure]]"], setting: ["finite sets"],
    }),
    // dangling local link
    entry("missing-premise", {
      label: "Missing premise", kind: "implies", ...SOURCE,
      premise: ["[[missing-record]]"], conclusion: ["[[measure-space]]"],
    }),
    // drafts: a node and a relation linking it
    draft("null-set", { label: "Null set", kind: "definition", ...SOURCE, requires: ["[[measure]]"] }),
    draft("null-set-implies-measure-zero", {
      label: "Null set implies measure zero", kind: "implies", ...SOURCE,
      premise: ["[[null-set]]"], conclusion: ["[[measure]]"],
    }),
    // a second source prefix; a pending term sharing a name key with "measurable space"; understanding absent
    entry("sigma-finite", {
      label: "Sigma-finite measure", kind: "definition", source: "papers/a.md", lines: "2-4", requires: ["Measurable-Space"],
    }),
    // a binary relation that is understood, from the second source
    entry("sigma-finite-generalizes-measure", {
      label: "Sigma-finite generalizes measure", kind: "generalizes", source: "papers/a.md", lines: "5", understanding: "understood",
      general: ["[[sigma-finite]]"], specific: ["[[measure]]"],
    }),
    // not records: other knowledge folders and ordinary notes are ignored
    { path: ".knowledge/sheets/notes/chapter.tex.md", frontmatter: {} },
    { path: "notes/chapter.md", frontmatter: { label: "Chapter", kind: "definition" } },
  ];
}

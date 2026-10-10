/**
 * The kgdistiller record format as the plugin needs it: the fixed keys, the
 * class rule and the textual value grammar. Everything here mirrors kgd's
 * records.py so the plugin never disagrees with `kgd check` or `kgd index`.
 */

/** The product's single knowledge tree at a base root. */
export const KNOWLEDGE_DIRECTORY = ".knowledge";
export const ENTRIES_DIRECTORY = `${KNOWLEDGE_DIRECTORY}/entries`;
export const DRAFTS_DIRECTORY = `${KNOWLEDGE_DIRECTORY}/drafts`;

export const UNDERSTANDING_STATES = ["unknown", "not-yet-understood", "understood"] as const;
export type Understanding = (typeof UNDERSTANDING_STATES)[number];

/** The only keys the product knows; every other list-valued key is a role. */
export const FIXED_KEYS: ReadonlySet<string> = new Set([
  "label", "kind", "source", "lines", "aliases", "understanding",
  "epistemic", "requires", "tags", "cssclasses",
]);

export type RecordClass = "node" | "relation";

/** The shared class rule: a relation iff a non-fixed key holds a non-empty list. */
export function recordClass(frontmatter: Readonly<Record<string, unknown>>): RecordClass {
  return Object.entries(frontmatter).some(
    ([key, value]) => !FIXED_KEYS.has(key) && Array.isArray(value) && value.length > 0,
  ) ? "relation" : "node";
}

const RECORD_PATH_RE = /^\.knowledge\/(entries|drafts)\/([^/]+)\.md$/;

/** Where a vault path sits in the knowledge tree, when it is a record or a draft. */
export function recordLocation(path: string): { folder: "entries" | "drafts"; id: string } | undefined {
  const match = RECORD_PATH_RE.exec(path);
  return match ? { folder: match[1] as "entries" | "drafts", id: match[2]! } : undefined;
}

export const isRecordPath = (path: string): boolean => recordLocation(path) !== undefined;

export const entryPath = (id: string): string => `${ENTRIES_DIRECTORY}/${id}.md`;
export const draftPath = (id: string): string => `${DRAFTS_DIRECTORY}/${id}.md`;

/**
 * NFKC plus full casefolding, as Python's `str.casefold`. JavaScript lowercasing
 * is simple case mapping, so the full foldings that differ are applied explicitly.
 */
export function fold(text: string): string {
  return text.normalize("NFKC").toLowerCase().replace(/ß/g, "ss").replace(/ς/g, "σ");
}

export type ValueError =
  | "stray-brackets" | "fragment" | "path-form" | "own-base" | "foreign-form" | "empty" | "multiline";

/** One `requires` or role value: a local link, a foreign link, a pending term, or a grammar error. */
export type ParsedValue =
  | { kind: "local"; id: string }
  | { kind: "foreign"; base: string; id: string }
  | { kind: "term"; term: string }
  | { kind: "error"; error: ValueError };

const LINK_RE = /^\[\[([^[\]]*)\]\]$/;
const BASE_RE = /^[a-z0-9][a-z0-9-]*$/;
const ENTRY_PATH_PREFIX = `${ENTRIES_DIRECTORY}/`;

/**
 * Apply the value grammar. `base` is the record's own base name when known; the
 * plugin cannot read the home registry, so without it a `[[<own base>:id]]`
 * link is drawn as foreign instead of being reported (kgd check reports it).
 */
export function parseValue(raw: string, base?: string): ParsedValue {
  const text = raw.trim();
  if (text.includes("\n")) return { kind: "error", error: "multiline" };
  if (!text.includes("[[") && !text.includes("]]")) {
    return text ? { kind: "term", term: text } : { kind: "error", error: "empty" };
  }
  const match = LINK_RE.exec(text);
  if (!match) return { kind: "error", error: "stray-brackets" };
  const linked = match[1]!.split("|", 1)[0]!.trim();
  if (!linked) return { kind: "error", error: "empty" };
  if (linked.includes("#") || linked.includes("^")) return { kind: "error", error: "fragment" };
  let target = fold(linked);
  if (target.endsWith(".md")) target = target.slice(0, -3);
  if (target.includes("/")) {
    const id = target.startsWith(ENTRY_PATH_PREFIX) ? target.slice(ENTRY_PATH_PREFIX.length) : "";
    if (!id || id.includes("/") || id.includes(":")) return { kind: "error", error: "path-form" };
    return { kind: "local", id };
  }
  const colon = target.indexOf(":");
  if (colon >= 0) {
    const foreign = target.slice(0, colon);
    const id = target.slice(colon + 1);
    if (base !== undefined && foreign === base) return { kind: "error", error: "own-base" };
    if (!BASE_RE.test(foreign) || !id || id.includes(":")) return { kind: "error", error: "foreign-form" };
    return { kind: "foreign", base: foreign, id };
  }
  return { kind: "local", id: target };
}

/**
 * Textual resolution of a local link to a record path: an accepted record
 * resolves only to `entries/<id>.md`; a draft also to `drafts/<id>.md`.
 */
export function resolveLocal(id: string, fromDraft: boolean, exists: (path: string) => boolean): string | undefined {
  if (exists(entryPath(id))) return entryPath(id);
  if (fromDraft && exists(draftPath(id))) return draftPath(id);
  return undefined;
}

/** The understanding state as kgd normalizes it: empty or absent means `unknown`. */
export function understandingOf(value: unknown): Understanding | undefined {
  if (value === undefined || value === null || value === "") return "unknown";
  return (UNDERSTANDING_STATES as readonly unknown[]).includes(value) ? (value as Understanding) : undefined;
}

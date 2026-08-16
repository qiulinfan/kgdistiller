import { append, badge, element, emptyState } from "../components/dom";
import { envelope, healthBadge, page, type ViewContext } from "./common";

export function renderSource(context: ViewContext): HTMLElement {
  const sourceResponse = envelope(context.state, "source");
  const root = page(sourceResponse?.result.source.path ?? "Source history", "Immutable captured versions");
  if (!sourceResponse) return root;
  const source = sourceResponse.result.source;
  const meta = element("div", "badge-row");
  append(meta, badge(source.format), healthBadge(source.status), badge(`${source.version_count} version${source.version_count === 1 ? "" : "s"}`));
  root.append(meta);
  const versions = envelope(context.state, "versions");
  if (versions) {
    const timeline = element("section", "timeline");
    append(timeline, element("h2", undefined, "Version history"));
    for (const version of versions.result.versions) {
      const item = element("article", "timeline-item");
      append(item, element("h3", undefined, `Version ${version.sequence}`), element("time", undefined, version.captured_at), badge(version.derivation_status ?? "not reviewed", version.derivation_status === "failed" ? "bad" : version.derivation_status === null ? "warn" : "neutral"), element("p", "muted", version.captured_path));
      timeline.append(item);
    }
    if (versions.result.truncated) {
      timeline.append(element("p", "notice notice-warn", `History is paginated. Older versions continue before sequence ${versions.result.next_before_sequence ?? "unknown"}.`));
    }
    root.append(timeline);
  }
  const diff = envelope(context.state, "diff");
  if (diff) {
    const section = element("section", "code-panel");
    append(section, element("h2", undefined, "Server-generated predecessor diff"));
    const pre = element("pre");
    pre.dir = "auto";
    pre.textContent = diff.result.text || "No textual change.";
    append(section, pre);
    if (diff.result.truncated) append(section, element("p", "notice notice-warn", "Diff output reached its server bound."));
    root.append(section);
  }
  const excerpt = envelope(context.state, "excerpt");
  if (excerpt) {
    const section = element("section", "excerpt-panel");
    append(section, element("h2", undefined, `Excerpt around line ${excerpt.result.line}`));
    const list = element("ol", "source-lines");
    list.start = excerpt.result.start;
    for (const row of excerpt.result.lines) {
      const item = element("li");
      item.value = row.number;
      item.dir = "auto";
      item.textContent = row.text || " ";
      if (row.number === excerpt.result.line) item.classList.add("focus-line");
      list.append(item);
    }
    append(section, list);
    if (excerpt.result.truncated) append(section, element("p", "notice notice-warn", "Surrounding context reached the excerpt byte bound; the focus line is retained."));
    root.append(section);
  }
  if (!versions && !diff && !excerpt) root.append(emptyState("Source details are loading", "History, server diff, and excerpt stay bound to the same generation."));
  return root;
}

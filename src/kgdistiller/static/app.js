const state = {
  payload: null,
  query: "",
  type: "all",
  origin: "all",
  selected: "",
  panel: "node",
  index: new Map(),
};

const queryInput = document.querySelector("#query");
const clearQuery = document.querySelector("#clear-query");
const results = document.querySelector("#results");
const resultCount = document.querySelector("#result-count");
const detail = document.querySelector("#detail");
const diagnostics = document.querySelector("#diagnostics");
const graph = document.querySelector("#graph");

const typeLabels = { field: "Field", topic: "Topic", knowledge: "Knowledge" };
const relationLabels = {
  contains: "contains",
  "prerequisite-for": "prerequisite for",
  implies: "implies",
  generalizes: "generalizes",
  "contrasts-with": "contrasts with",
  "derived-from": "derived from",
};

function element(name, className, text) {
  const node = document.createElement(name);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function button(className, text, action) {
  const node = element("button", className, text);
  node.type = "button";
  node.addEventListener("click", action);
  return node;
}

function list(value) {
  return Array.isArray(value) ? value.map(String).filter(Boolean) : value ? [String(value)] : [];
}

function properties(node) { return node.properties || {}; }
function propertyText(node, key) {
  const value = properties(node)[key];
  return value === undefined || value === null ? "" : String(value);
}
function normalize(value) { return String(value || "").normalize("NFKC").toLocaleLowerCase(); }
function safeExternalHref(value) {
  try {
    const parsed = new URL(String(value || ""));
    return parsed.protocol === "https:" || parsed.protocol === "http:" ? parsed.href : "";
  } catch (_error) {
    return "";
  }
}
function isResearch(node) {
  return node.type === "knowledge" && propertyText(node, "knowledge_origin") === "research";
}
function originLabel(node) { return isResearch(node) ? "Research" : "Personal note"; }
function searchable(node) {
  return normalize([
    node.label,
    node.id,
    node.text,
    JSON.stringify(node.entry || {}),
    ...list(properties(node).aliases),
    propertyText(node, "course"),
    propertyText(node, "topic"),
    ...list(properties(node).fields),
  ].join(" "));
}

function searchScore(node, terms) {
  if (!terms.length) {
    const evidence = Number(properties(node).evidence_count || 0);
    const typeBias = node.type === "knowledge" ? 80 : node.type === "topic" ? 40 : 0;
    return typeBias + Math.min(Number.isFinite(evidence) ? evidence : 0, 20);
  }
  const label = normalize(node.label);
  const aliases = normalize(list(properties(node).aliases).join(" "));
  const id = normalize(node.id);
  const haystack = searchable(node);
  if (!terms.every((term) => haystack.includes(term))) return -1;
  let score = 0;
  for (const term of terms) {
    if (label === term) score += 160;
    else if (label.startsWith(term)) score += 100;
    else if (label.includes(term)) score += 70;
    if (aliases.includes(term)) score += 55;
    if (id.includes(term)) score += 25;
  }
  if (node.type === "knowledge") score += 14;
  if (node.type === "topic") score += 8;
  return score;
}

function filteredNodes() {
  const terms = normalize(state.query).split(/\s+/).filter(Boolean);
  return state.payload.nodes
    .filter((node) => state.type === "all" || node.type === state.type)
    .filter((node) => state.origin === "all" || propertyText(node, "knowledge_origin") === state.origin)
    .map((node) => ({ node, score: searchScore(node, terms) }))
    .filter((item) => item.score >= 0)
    .sort((left, right) => right.score - left.score || left.node.label.localeCompare(right.node.label))
    .slice(0, 100)
    .map((item) => item.node);
}

function renderResults() {
  const nodes = filteredNodes();
  resultCount.textContent = `${nodes.length}${nodes.length === 100 ? "+" : ""}`;
  results.replaceChildren();
  if (!nodes.length) {
    const empty = element("li", "empty-list");
    empty.append(element("strong", "", "No matching nodes"), element("span", "", "Try a name, alias, or entry keyword."));
    results.append(empty);
    return;
  }
  for (const node of nodes) {
    const item = element("li");
    const itemButton = button(node.id === state.selected ? "active" : "", "", () => selectNode(node.id));
    itemButton.append(element("span", `dot ${node.type}${isResearch(node) ? " research" : ""}`));
    const copy = element("span", "result-copy");
    const subtitle = node.type === "knowledge" ? `${typeLabels[node.type]} · ${originLabel(node)}` : `${typeLabels[node.type] || node.type} · ${node.id}`;
    copy.append(element("strong", "", node.label), element("small", "", subtitle));
    itemButton.append(copy);
    item.append(itemButton);
    results.append(item);
  }
}

function related(id) {
  return state.payload.edges.flatMap((edge) => {
    if (edge.source === id) return [{ edge, node: state.index.get(edge.target), direction: "out" }];
    if (edge.target === id) return [{ edge, node: state.index.get(edge.source), direction: "in" }];
    return [];
  }).filter((item) => item.node).sort((a, b) =>
    a.edge.relation.localeCompare(b.edge.relation) || a.node.label.localeCompare(b.node.label));
}

function appendSection(parent, title, content, className = "") {
  const section = element("section", `section ${className}`.trim());
  section.append(element("h3", "", title), content);
  parent.append(section);
  return section;
}

function addAttribute(parent, label, value) {
  if (!value) return;
  const row = element("div", "attribute-row");
  row.append(element("span", "", label), element("strong", "", value));
  parent.append(row);
}

function formattedEntryValue(node, key) {
  const value = node.entry?.[key];
  if (Array.isArray(value)) {
    return value.map((item) => typeof item === "object" ? JSON.stringify(item, null, 2) : String(item)).join("\n");
  }
  if (value && typeof value === "object") return JSON.stringify(value, null, 2);
  return value === undefined || value === null ? "" : String(value);
}

async function showSource(authority, line, title = "Source excerpt") {
  if (!authority) return;
  let section = detail.querySelector("#source-section");
  if (!section) {
    section = element("section", "section source-section");
    section.id = "source-section";
    section.append(element("h3"), element("p", "source-path"), element("div", "source"));
    detail.append(section);
  }
  section.querySelector("h3").textContent = title;
  section.querySelector(".source-path").textContent = `${authority}:${line || 1}`;
  const code = section.querySelector(".source");
  code.textContent = "Loading source excerpt…";
  section.scrollIntoView({ block: "nearest", behavior: "smooth" });
  try {
    const snapshot = state.payload?.manifest?.snapshot_sha256 || "";
    const response = await fetch(`/api/source?path=${encodeURIComponent(authority)}&line=${encodeURIComponent(line || 1)}&snapshot=${encodeURIComponent(snapshot)}`);
    const excerpt = await response.json();
    if (!response.ok) throw new Error(excerpt.error || "Unable to read source");
    code.replaceChildren();
    for (const row of excerpt.lines) {
      const sourceLine = element("div", row.number === excerpt.line ? "focus" : "");
      sourceLine.append(element("span", "line-number", String(row.number)), element("span", "", row.text));
      code.append(sourceLine);
    }
  } catch (error) {
    code.textContent = String(error);
  }
}

async function copyReference(node, control) {
  const authority = node.provenance?.authority;
  const value = authority ? `${node.id}\n${authority}` : node.id;
  try {
    if (navigator.clipboard?.writeText) {
      await navigator.clipboard.writeText(value);
    } else {
      const input = element("textarea");
      input.value = value;
      input.className = "clipboard-fallback";
      document.body.append(input);
      input.select();
      document.execCommand("copy");
      input.remove();
    }
    control.textContent = "Copied";
    window.setTimeout(() => { control.textContent = "Copy reference"; }, 1600);
  } catch (error) {
    control.textContent = "Copy failed";
    control.title = String(error);
  }
}

function renderDetail(node) {
  detail.replaceChildren();
  const heading = element("header", "node-heading");
  heading.append(element("span", `type ${isResearch(node) ? "research" : ""}`, typeLabels[node.type] || node.type));
  heading.append(element("h2", "", node.label), element("code", "node-id", node.id));
  detail.append(heading);

  addAttribute(detail, "Kind", propertyText(node, "kind"));
  addAttribute(detail, "Source status", propertyText(node, "source_status"));
  addAttribute(detail, "Source format", propertyText(node, "source_format"));
  if (node.type === "knowledge") addAttribute(detail, "Knowledge origin", originLabel(node));
  addAttribute(detail, "Course", propertyText(node, "course"));
  const fields = list(properties(node).fields).map((id) => state.index.get(id)?.label || id);
  addAttribute(detail, "Fields", fields.join(" · "));

  const aliases = list(properties(node).aliases);
  if (aliases.length) {
    const chips = element("div", "chips");
    for (const alias of aliases) chips.append(element("span", "", alias));
    appendSection(detail, "Aliases", chips);
  }
  if (node.text) appendSection(detail, "Entry", element("div", "evidence", node.text));
  for (const [key, label] of [
    ["context", "Context"],
    ["role", "Role in source"],
    ["prerequisites", "Direct prerequisites"],
    ["common_confusions", "Common confusions"],
    ["open_questions", "Open questions"],
    ["sources", "Source locations"],
  ]) {
    const value = formattedEntryValue(node, key);
    if (value) appendSection(detail, label, element("div", "evidence compact", value));
  }

  const relations = related(node.id);
  if (relations.length) {
    const relationList = element("div", "relation-list");
    for (const item of relations.slice(0, 48)) {
      const relationButton = button("relation-item", "", () => selectNode(item.node.id));
      relationButton.append(element("span", "relation-arrow", item.direction === "out" ? "→" : "←"));
      const copy = element("span");
      copy.append(element("small", "", relationLabels[item.edge.relation] || item.edge.relation), element("strong", "", item.node.label));
      relationButton.append(copy);
      relationList.append(relationButton);
    }
    appendSection(detail, `Relations · ${relations.length}`, relationList);
  }

  const backlinks = state.payload.references
    .filter((reference) => reference.target === node.id)
    .sort((a, b) => a.authority.localeCompare(b.authority) || Number(a.line) - Number(b.line));
  if (backlinks.length) {
    const backlinkList = element("div", "relation-list");
    for (const reference of backlinks) {
      const row = element("div", "backlink-row");
      const local = button("relation-item backlink", "", () => void showSource(reference.authority, reference.line, "Backlink source"));
      local.append(element("span", "relation-arrow", "↩"));
      const copy = element("span");
      copy.append(element("small", "", reference.label || reference.source_name || "Reference"), element("strong", "", `${reference.authority}:${reference.line}`));
      local.append(copy);
      row.append(local);
      const referenceHref = safeExternalHref(reference.web);
      if (referenceHref) {
        const external = element("a", "external-link", "↗");
        external.href = referenceHref;
        external.target = "_blank";
        external.rel = "noreferrer";
        external.title = "Open canonical backlink";
        row.append(external);
      }
      backlinkList.append(row);
    }
    appendSection(detail, `Backlinks · ${backlinks.length}`, backlinkList);
  }

  const actions = element("div", "source-actions");
  const copyControl = button("", "Copy reference", () => void copyReference(node, copyControl));
  actions.append(copyControl);
  const provenance = node.provenance || {};
  if (provenance.authority) {
    actions.append(button("", "View local source", () => void showSource(provenance.authority, provenance.line || 1)));
  }
  const provenanceHref = safeExternalHref(provenance.web);
  if (provenanceHref) {
    const canonical = element("a", "", "Open canonical definition ↗");
    canonical.href = provenanceHref;
    canonical.target = "_blank";
    canonical.rel = "noreferrer";
    actions.append(canonical);
  }
  detail.append(actions);
  if (provenance.authority) detail.append(element("p", "source-path", `${provenance.authority}:${provenance.line || 1}`));
}

function svgElement(name, attributes = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  return node;
}

function graphSlice(selected) {
  const rings = new Map([[selected.id, 0]]);
  const firstEdges = state.payload.edges.filter((edge) => edge.source === selected.id || edge.target === selected.id);
  const firstIds = [...new Set(firstEdges.map((edge) => edge.source === selected.id ? edge.target : edge.source))].slice(0, 24);
  for (const id of firstIds) rings.set(id, 1);
  const secondIds = [];
  for (const firstId of firstIds) {
    for (const edge of state.payload.edges) {
      if (secondIds.length >= 28) break;
      if (edge.source !== firstId && edge.target !== firstId) continue;
      const other = edge.source === firstId ? edge.target : edge.source;
      if (!rings.has(other)) {
        rings.set(other, 2);
        secondIds.push(other);
      }
    }
    if (secondIds.length >= 28) break;
  }
  const ids = new Set([selected.id, ...firstIds, ...secondIds]);
  return {
    rings,
    nodes: [...ids].map((id) => state.index.get(id)).filter(Boolean),
    edges: state.payload.edges.filter((edge) => ids.has(edge.source) && ids.has(edge.target)),
  };
}

function renderGraph(selected) {
  graph.replaceChildren();
  const width = Math.max(graph.clientWidth, 420);
  const height = Math.max(graph.clientHeight, 420);
  graph.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const { nodes, edges, rings } = graphSlice(selected);
  const center = { x: width / 2, y: height / 2 };
  const shortest = Math.min(width, height);
  const ringRadii = { 1: Math.max(90, shortest * .25), 2: Math.max(150, shortest * .42) };
  const positions = new Map([[selected.id, { ...center, radius: 13, ring: 0 }]]);
  for (const ring of [1, 2]) {
    const group = nodes.filter((node) => rings.get(node.id) === ring);
    group.forEach((node, index) => {
      const angle = -Math.PI / 2 + index * Math.PI * 2 / Math.max(group.length, 1);
      positions.set(node.id, {
        x: center.x + Math.cos(angle) * ringRadii[ring],
        y: center.y + Math.sin(angle) * ringRadii[ring],
        radius: ring === 1 ? 8.5 : 5.5,
        ring,
      });
    });
  }

  const defs = svgElement("defs");
  const marker = svgElement("marker", { id: "arrow", markerWidth: 7, markerHeight: 7, refX: 6, refY: 3.5, orient: "auto", markerUnits: "strokeWidth" });
  marker.append(svgElement("path", { d: "M0,0 L7,3.5 L0,7 Z", class: "arrow" }));
  defs.append(marker);
  graph.append(defs);

  for (const edge of edges) {
    const from = positions.get(edge.source);
    const to = positions.get(edge.target);
    if (!from || !to) continue;
    const angle = Math.atan2(to.y - from.y, to.x - from.x);
    const line = svgElement("line", {
      x1: from.x + Math.cos(angle) * (from.radius + 2),
      y1: from.y + Math.sin(angle) * (from.radius + 2),
      x2: to.x - Math.cos(angle) * (to.radius + 7),
      y2: to.y - Math.sin(angle) * (to.radius + 7),
      class: `edge ${edge.relation === "contains" ? "structure" : "semantic"}`,
      "marker-end": "url(#arrow)",
    });
    const title = svgElement("title");
    title.textContent = relationLabels[edge.relation] || edge.relation;
    line.append(title);
    graph.append(line);
  }

  for (const node of nodes.sort((a, b) => (rings.get(b.id) || 0) - (rings.get(a.id) || 0))) {
    const point = positions.get(node.id);
    if (!point) continue;
    const group = svgElement("g", { class: `node ring-${point.ring}`, tabindex: "0", role: "button", "aria-label": node.label });
    const shapeAttributes = { class: `${node.type}${isResearch(node) ? " research" : ""}` };
    if (isResearch(node)) {
      group.append(svgElement("rect", { x: point.x - point.radius, y: point.y - point.radius, width: point.radius * 2, height: point.radius * 2, rx: 1.5, ...shapeAttributes }));
    } else {
      group.append(svgElement("circle", { cx: point.x, cy: point.y, r: point.radius, ...shapeAttributes }));
    }
    if (point.ring <= 1) {
      const label = svgElement("text", { x: point.x, y: point.y + point.radius + 15, "text-anchor": "middle", class: point.ring === 0 ? "selected-label" : "" });
      label.textContent = node.label.length > 30 ? `${node.label.slice(0, 29)}…` : node.label;
      group.append(label);
    }
    const title = svgElement("title");
    title.textContent = `${node.label} · ${typeLabels[node.type] || node.type}${node.type === "knowledge" ? ` · ${originLabel(node)}` : ""}`;
    group.append(title);
    group.addEventListener("click", () => selectNode(node.id));
    group.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        selectNode(node.id);
      }
    });
    graph.append(group);
  }
}

function allDiagnostics() {
  const source = state.payload?.diagnostics || {};
  return ["errors", "warnings", "info"].flatMap((severity) =>
    (Array.isArray(source[severity]) ? source[severity] : []).map((item) => ({ ...item, severity })));
}

function renderDiagnostics() {
  const items = allDiagnostics();
  diagnostics.replaceChildren();
  const summary = element("div", "diagnostic-summary");
  summary.append(element("strong", "", String(items.length)), element("span", "", "visible graph diagnostics"));
  summary.append(element("p", "", "Unresolved references and quality findings remain explicit; the browser does not discard them."));
  diagnostics.append(summary);
  if (!items.length) diagnostics.append(element("p", "empty", "No diagnostics in this graph."));
  for (const item of items) {
    const enabled = item.node && state.index.has(item.node);
    const control = button(`diagnostic-item ${item.severity}`, "", () => enabled && selectNode(item.node));
    control.disabled = !enabled;
    control.append(element("span", "", `${item.severity} · ${item.code}`), element("strong", "", item.message));
    if (item.source) control.append(element("small", "", item.source));
    diagnostics.append(control);
  }
}

function showPanel(name) {
  state.panel = name;
  detail.hidden = name !== "node";
  diagnostics.hidden = name !== "diagnostics";
  document.querySelectorAll("[data-panel]").forEach((control) => {
    const active = control.dataset.panel === name;
    control.classList.toggle("active", active);
    control.setAttribute("aria-selected", String(active));
  });
}

function selectNode(id, updateHash = true) {
  const node = state.index.get(id);
  if (!node) return;
  state.selected = id;
  if (updateHash) history.replaceState(null, "", `#node=${encodeURIComponent(id)}`);
  showPanel("node");
  renderResults();
  renderDetail(node);
  renderGraph(node);
}

function activateFilter(selector, control, key) {
  document.querySelectorAll(selector).forEach((item) => item.classList.remove("active"));
  control.classList.add("active");
  state[key] = control.dataset[key];
  renderResults();
}

async function start() {
  const response = await fetch("/api/graph.json");
  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  state.payload = await response.json();
  state.index = new Map(state.payload.nodes.map((node) => [node.id, node]));
  const counts = state.payload.manifest?.counts || {
    nodes: state.payload.nodes.length,
    edges: state.payload.edges.length,
    references: state.payload.references.length,
  };
  const diagnosticItems = allDiagnostics();
  document.querySelector("#stats").textContent = `${counts.nodes} nodes · ${counts.edges} edges · ${counts.references} references · ${diagnosticItems.length} diagnostics`;
  document.querySelector("#diagnostic-count").textContent = String(diagnosticItems.length);

  queryInput.addEventListener("input", () => {
    state.query = queryInput.value;
    clearQuery.hidden = !state.query;
    renderResults();
  });
  clearQuery.addEventListener("click", () => {
    queryInput.value = "";
    state.query = "";
    clearQuery.hidden = true;
    queryInput.focus();
    renderResults();
  });
  document.querySelectorAll("[data-type]").forEach((control) =>
    control.addEventListener("click", () => activateFilter("[data-type]", control, "type")));
  document.querySelectorAll("[data-origin]").forEach((control) =>
    control.addEventListener("click", () => activateFilter("[data-origin]", control, "origin")));
  document.querySelectorAll("[data-panel]").forEach((control) =>
    control.addEventListener("click", () => showPanel(control.dataset.panel)));
  window.addEventListener("hashchange", () => {
    const requested = new URLSearchParams(location.hash.slice(1)).get("node");
    if (requested) selectNode(requested, false);
  });
  window.addEventListener("resize", () => {
    const selected = state.index.get(state.selected);
    if (selected) renderGraph(selected);
  });

  renderDiagnostics();
  renderResults();
  const requested = new URLSearchParams(location.hash.slice(1)).get("node");
  const first = requested && state.index.has(requested) ? requested :
    state.payload.nodes.find((node) => node.type === "knowledge")?.id || state.payload.nodes[0]?.id;
  if (first) selectNode(first, false);
}

start().catch((error) => {
  detail.textContent = `Unable to load graph: ${error}`;
});

const state = { payload: null, query: "", type: "all", selected: "", index: new Map() };
const query = document.querySelector("#query");
const results = document.querySelector("#results");
const detail = document.querySelector("#detail");
const graph = document.querySelector("#graph");

function element(name, className, text) {
  const node = document.createElement(name);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

function list(value) { return Array.isArray(value) ? value.map(String) : value ? [String(value)] : []; }
function properties(node) { return node.properties || {}; }
function normalize(value) { return String(value || "").normalize("NFKC").toLocaleLowerCase(); }
function searchable(node) { return normalize([node.id, node.label, node.text, JSON.stringify(node.entry || {}), ...list(properties(node).aliases), ...list(properties(node).fields)].join(" ")); }

function filteredNodes() {
  const terms = normalize(state.query).split(/\s+/).filter(Boolean);
  return state.payload.nodes
    .filter((node) => state.type === "all" || node.type === state.type)
    .filter((node) => terms.every((term) => searchable(node).includes(term)))
    .sort((a, b) => {
      const ak = a.type === "knowledge" ? 0 : a.type === "topic" ? 1 : 2;
      const bk = b.type === "knowledge" ? 0 : b.type === "topic" ? 1 : 2;
      return ak - bk || a.label.localeCompare(b.label);
    })
    .slice(0, 100);
}

function renderResults() {
  results.replaceChildren();
  for (const node of filteredNodes()) {
    const item = element("li");
    const button = element("button", node.id === state.selected ? "active" : "");
    button.type = "button";
    button.addEventListener("click", () => selectNode(node.id));
    button.append(element("span", `dot ${node.type}`));
    const copy = element("span", "result-copy");
    copy.append(element("strong", "", node.label), element("small", "", `${node.type} · ${node.id}`));
    button.append(copy); item.append(button); results.append(item);
  }
}

function related(id) {
  return state.payload.edges.flatMap((edge) => {
    if (edge.source === id) return [{ edge, node: state.index.get(edge.target), direction: "out" }];
    if (edge.target === id) return [{ edge, node: state.index.get(edge.source), direction: "in" }];
    return [];
  }).filter((item) => item.node);
}

function appendSection(title, content) {
  const section = element("section", "section");
  section.append(element("h2", "", title), content);
  detail.append(section);
}

async function renderSource(node) {
  const provenance = node.provenance || {};
  if (!provenance.authority) return;
  const wrap = element("div");
  wrap.append(element("p", "node-id", `${provenance.authority}:${provenance.line || 1}`));
  const code = element("div", "source", "Loading source excerpt…");
  wrap.append(code); appendSection("Source", wrap);
  try {
    const response = await fetch(`/api/source?path=${encodeURIComponent(provenance.authority)}&line=${provenance.line || 1}`);
    const excerpt = await response.json();
    if (!response.ok) throw new Error(excerpt.error || "Unable to read source");
    code.replaceChildren();
    for (const row of excerpt.lines) {
      const line = element("div", row.number === excerpt.line ? "focus" : "");
      line.append(element("span", "", String(row.number)), element("span", "", row.text));
      code.append(line);
    }
  } catch (error) { code.textContent = String(error); }
}

function renderDetail(node) {
  detail.replaceChildren();
  detail.append(element("span", "type", node.type), element("h1", "", node.label), element("p", "node-id", node.id));
  const fields = list(properties(node).fields).map((id) => state.index.get(id)?.label || id);
  if (fields.length) detail.append(element("p", "node-id", fields.join(" · ")));
  const text = node.text || node.entry?.summary;
  if (text) detail.append(element("p", "entry", text));
  const relations = related(node.id);
  if (relations.length) {
    const listNode = element("ul", "relations");
    for (const item of relations) {
      const li = element("li"); const button = element("button");
      const label = item.direction === "out" ? `${item.edge.relation} →` : `← ${item.edge.relation}`;
      button.append(element("span", "", item.node.label), element("small", "", label));
      button.addEventListener("click", () => selectNode(item.node.id)); li.append(button); listNode.append(li);
    }
    appendSection("Relations", listNode);
  }
  const refs = state.payload.references.filter((reference) => reference.target === node.id);
  if (refs.length) {
    const refList = element("ul", "refs");
    for (const ref of refs) {
      const li = element("li", "", ref.authority);
      li.append(element("small", "", ` · line ${ref.line}`)); refList.append(li);
    }
    appendSection("Backlinks", refList);
  }
  void renderSource(node);
}

function svgElement(name, attributes = {}) {
  const node = document.createElementNS("http://www.w3.org/2000/svg", name);
  for (const [key, value] of Object.entries(attributes)) node.setAttribute(key, String(value));
  return node;
}

function renderGraph(node) {
  graph.replaceChildren();
  const width = Math.max(graph.clientWidth, 420); const height = Math.max(graph.clientHeight, 420);
  graph.setAttribute("viewBox", `0 0 ${width} ${height}`);
  const neighbors = related(node.id).slice(0, 30); const cx = width / 2; const cy = height / 2;
  const radius = Math.min(width, height) * .34;
  const positions = new Map([[node.id, { x: cx, y: cy }]]);
  neighbors.forEach((item, index) => positions.set(item.node.id, { x: cx + Math.cos(-Math.PI / 2 + index * Math.PI * 2 / neighbors.length) * radius, y: cy + Math.sin(-Math.PI / 2 + index * Math.PI * 2 / neighbors.length) * radius }));
  for (const item of neighbors) {
    const from = positions.get(node.id); const to = positions.get(item.node.id);
    graph.append(svgElement("line", { x1: from.x, y1: from.y, x2: to.x, y2: to.y, class: `edge ${item.edge.relation === "contains" ? "" : "semantic"}` }));
    const label = svgElement("text", { x: (from.x + to.x) / 2, y: (from.y + to.y) / 2, class: "edge-label", "text-anchor": "middle" });
    label.textContent = item.edge.relation; graph.append(label);
  }
  const visible = [node, ...neighbors.map((item) => item.node)];
  for (const item of visible) {
    const point = positions.get(item.id); const group = svgElement("g", { class: "node", tabindex: "0" });
    const color = `var(--${item.type})`; group.append(svgElement("circle", { cx: point.x, cy: point.y, r: item.id === node.id ? 13 : 8, fill: color }));
    const label = svgElement("text", { x: point.x, y: point.y + (item.id === node.id ? 29 : 23), "text-anchor": "middle" });
    label.textContent = item.label.length > 28 ? `${item.label.slice(0, 27)}…` : item.label; group.append(label);
    group.addEventListener("click", () => selectNode(item.id)); group.addEventListener("keydown", (event) => { if (event.key === "Enter") selectNode(item.id); }); graph.append(group);
  }
}

function selectNode(id) {
  const node = state.index.get(id); if (!node) return;
  state.selected = id; history.replaceState(null, "", `#node=${encodeURIComponent(id)}`);
  renderResults(); renderDetail(node); renderGraph(node);
}

async function start() {
  const response = await fetch("/api/graph.json"); state.payload = await response.json();
  state.index = new Map(state.payload.nodes.map((node) => [node.id, node]));
  const counts = state.payload.manifest.counts;
  document.querySelector("#stats").textContent = `${counts.nodes} nodes · ${counts.edges} edges · ${counts.references} references`;
  query.addEventListener("input", () => { state.query = query.value; renderResults(); });
  document.querySelectorAll("[data-type]").forEach((button) => button.addEventListener("click", () => {
    document.querySelectorAll("[data-type]").forEach((item) => item.classList.remove("active")); button.classList.add("active"); state.type = button.dataset.type; renderResults();
  }));
  renderResults();
  const requested = new URLSearchParams(location.hash.slice(1)).get("node");
  const first = requested && state.index.has(requested) ? requested : state.payload.nodes.find((node) => node.type === "knowledge")?.id || state.payload.nodes[0]?.id;
  if (first) selectNode(first);
}

start().catch((error) => { detail.textContent = `Unable to load graph: ${error}`; });

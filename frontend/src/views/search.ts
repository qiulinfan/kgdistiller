import type { SearchNode } from "../api/contracts";
import { append, badge, element, emptyState, link } from "../components/dom";
import { routeHref } from "../state/router";
import { envelope, page, renderOmissions, type ViewContext } from "./common";

function resultCard(node: SearchNode): HTMLElement {
  const card = element("article", "result-card");
  const heading = element("h2");
  append(heading, link(node.label, routeHref({ name: "node", handle: node.handle })));
  append(card, heading, element("p", "handle", node.handle));
  const lanes = element("ul", "lane-list");
  for (const lane of node.lane_evidence) {
    const row = element("li");
    append(row, badge(lane.lane), document.createTextNode(` ${lane.reason} · ${lane.score.toFixed(3)}`));
    if (lane.matched_terms.length) append(row, element("span", "muted", ` · ${lane.matched_terms.join(", ")}`));
    lanes.append(row);
  }
  append(card, lanes);
  return card;
}

export function renderSearch({ state }: ViewContext): HTMLElement {
  const root = page("Federated search", "Identity remains Vault-qualified");
  const form = element("form", "search-form");
  form.setAttribute("role", "search");
  const input = element("input");
  input.name = "q";
  input.type = "search";
  input.maxLength = 4096;
  input.required = true;
  input.value = state.route.name === "search" ? state.route.query : "";
  input.placeholder = "Concept, alias, phrase, or handle";
  input.setAttribute("aria-label", "Search knowledge");
  const submit = element("button", "button button-primary", "Search");
  submit.type = "submit";
  form.addEventListener("submit", (event) => {
    event.preventDefault();
    const text = input.value.trim();
    if (!text) return;
    const vault = state.route.name === "search" ? state.route.vault : null;
    const scope = state.route.name === "search" ? state.route.scope : null;
    location.hash = routeHref({ name: "search", query: text, vault, scope });
  });
  append(form, input, submit);
  root.append(form);
  const response = envelope(state, "search");
  if (!response) {
    if (state.route.name === "search" && !state.route.query) root.append(emptyState("Start with a precise phrase", "Results explain whether identity, taxonomy, lexical, or graph evidence caused each match."));
    return root;
  }
  if (response.result.resolutions.length) {
    const resolutions = element("section", "resolution-strip");
    for (const item of response.result.resolutions) append(resolutions, badge(`${item.query}: ${item.status}`, item.status === "missing" ? "warn" : "neutral"));
    root.append(resolutions);
  }
  const list = element("section", "result-list");
  for (const node of response.result.nodes) list.append(resultCard(node));
  root.append(response.result.nodes.length ? list : emptyState("No matching concepts", "Try another phrase or select a different Vault."));
  const omissions = renderOmissions(response.result.omissions, response.result.truncated);
  if (omissions) root.append(omissions);
  return root;
}

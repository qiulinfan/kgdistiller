import { append, badge, element, formatDigest, link } from "../components/dom";
import { routeHref } from "../state/router";
import { page, type ViewContext } from "./common";

export function renderHome({ state }: ViewContext): HTMLElement {
  const root = page("Your knowledge, across Vaults", "Read-only local workspace");
  const intro = element("p", "lede", "Search, browse, and inspect source-backed concepts without merging Vault identities.");
  root.append(intro);
  const actions = element("div", "button-row");
  append(actions, link("Search all Vaults", routeHref({ name: "search", query: "", vault: null, scope: null }), "button button-primary"), link("Browse taxonomy", routeHref({ name: "browse", vault: null }), "button"));
  root.append(actions);
  if (state.status) {
    const metrics = element("section", "metric-grid");
    for (const [label, value] of [
      ["Registered", state.status.registered_vaults],
      ["Healthy", state.status.healthy_vaults],
      ["Incomplete", state.status.incomplete_vaults]
    ] as const) {
      const card = element("article", "metric-card");
      append(card, element("strong", undefined, String(value)), element("span", undefined, label));
      metrics.append(card);
    }
    root.append(metrics);
  }
  const cards = element("section", "card-grid");
  cards.setAttribute("aria-label", "Available Vaults");
  for (const vault of state.vaults) {
    const card = element("article", "card");
    const heading = element("h2");
    append(heading, link(vault.label, routeHref({ name: "browse", vault: vault.vault_id })));
    append(card, heading, badge(vault.vault_id), element("p", undefined, `${vault.counts.nodes} nodes · ${vault.counts.edges} edges · ${vault.counts.documents} sources`));
    const freshness = element("p", "muted", `Sources: ${vault.source_freshness.current} current, ${vault.source_freshness.changed} changed, ${vault.source_freshness.missing} missing, ${vault.source_freshness.unavailable} unavailable`);
    const generation = element("code", "digest", formatDigest(vault.generation));
    append(card, freshness, generation);
    cards.append(card);
  }
  if (state.vaults.length) root.append(cards);
  return root;
}

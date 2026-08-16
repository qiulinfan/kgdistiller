import { append, badge, element, formatDigest } from "../components/dom";
import { page, type ViewContext } from "./common";

export function renderHealth({ state }: ViewContext): HTMLElement {
  const root = page("Workspace health", "Generation-bound diagnostics");
  const generation = element("section", "card");
  append(generation, element("h2", undefined, "Federation generation"), element("code", "digest", formatDigest(state.generation)));
  root.append(generation);
  if (state.incompleteVaults.length) {
    const failures = element("section", "card-grid");
    for (const item of state.incompleteVaults) {
      const card = element("article", "card");
      append(card, element("h2", undefined, item.vault_id), badge(item.code, "bad"), element("p", undefined, item.message));
      failures.append(card);
    }
    root.append(failures);
  }
  const vaults = element("section", "health-table");
  for (const vault of state.vaults) {
    const card = element("article", "card");
    append(card, element("h2", undefined, vault.label), badge(vault.health, "good"), element("p", undefined, `Graph ${formatDigest(vault.graph_sha256)} · Source ${formatDigest(vault.source_ledger_generation_sha256)}`), element("p", "muted", `${vault.source_freshness.current} current · ${vault.source_freshness.changed} changed · ${vault.source_freshness.missing} missing · ${vault.source_freshness.unavailable} unavailable`));
    vaults.append(card);
  }
  root.append(vaults);
  return root;
}

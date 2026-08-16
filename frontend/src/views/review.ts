import { append, badge, element, emptyState, link } from "../components/dom";
import { routeHref } from "../state/router";
import { envelope, page, renderOmissions, type ViewContext } from "./common";

export function renderReview(context: ViewContext): HTMLElement {
  const root = page("Review queue", "Stale, pending, and failed records");
  if (context.state.route.name !== "review" || context.state.route.vault === null) {
    root.append(emptyState("Choose a Vault", "Review state is evaluated independently for each Vault."));
    return root;
  }
  const response = envelope(context.state, "stale");
  if (!response) return root;
  const list = element("section", "review-list");
  for (const item of response.result.items) {
    const card = element("article", "card review-card");
    if (item.kind === "node") {
      const heading = element("h2");
      append(heading, link(item.node.label, routeHref({ name: "node", handle: item.node.handle })));
      append(card, heading, badge(item.reason, "warn"), element("p", "handle", item.node.handle));
    } else if (item.kind === "edge") {
      append(card, element("h2", undefined, item.edge.relation), badge(item.reason, "warn"), element("p", undefined, `${item.edge.source} → ${item.edge.target}`));
    } else {
      const heading = element("h2");
      append(heading, link(item.source.path, routeHref({ name: "source", vault: item.source.vault_id, document: item.source.document_id })));
      append(card, heading, badge(item.reason, item.reason === "failed" ? "bad" : "warn"));
    }
    list.append(card);
  }
  root.append(response.result.items.length ? list : emptyState("Nothing needs review", "This Vault has no stale or failed records in the current generation."));
  if (response.result.next_cursor) {
    const next = link("Next page", routeHref({ name: "review", vault: context.state.route.vault, cursor: response.result.next_cursor }), "button");
    root.append(next);
  }
  const omissions = renderOmissions(response.result.omissions, response.result.truncated);
  if (omissions) root.append(omissions);
  return root;
}

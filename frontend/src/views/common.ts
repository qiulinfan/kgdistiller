import type { ApiEnvelope, ApiResult, Omission } from "../api/contracts";
import type { WorkspaceState } from "../state/model";
import type { Route } from "../state/router";
import { routeHref } from "../state/router";
import { append, badge, element, link } from "../components/dom";

export interface ViewContext {
  state: WorkspaceState;
  go: (route: Route) => void;
}

export function envelope<K extends ApiResult["kind"]>(state: WorkspaceState, kind: K): ApiEnvelope<Extract<ApiResult, { kind: K }>> | null {
  const rows = state.response === null ? state.related : [state.response, ...state.related];
  const match = rows.find((row) => row.result.kind === kind);
  return (match ?? null) as ApiEnvelope<Extract<ApiResult, { kind: K }>> | null;
}

export function page(title: string, eyebrow?: string): HTMLElement {
  const section = element("section", "view");
  const header = element("header", "view-header");
  if (eyebrow) append(header, element("p", "eyebrow", eyebrow));
  append(header, element("h1", undefined, title));
  section.append(header);
  return section;
}

export function renderOmissions(omissions: Omission[], truncated: boolean): HTMLElement | null {
  if (!truncated && omissions.length === 0) return null;
  const box = element("section", "notice notice-warn");
  append(box, element("h2", undefined, "Partial result"));
  const summary = element("p", undefined, omissions.length ? `${omissions.length} bounded omission${omissions.length === 1 ? "" : "s"}.` : "The server bounded this result.");
  append(box, summary);
  if (omissions.length) {
    const list = element("ul");
    for (const omission of omissions) append(list, element("li", undefined, `${omission.kind}: ${omission.id} (${omission.reason})`));
    append(box, list);
  }
  return box;
}

export function renderClientNotices(notices: string[]): HTMLElement | null {
  if (!notices.length) return null;
  const box = element("section", "notice notice-warn");
  append(box, element("h2", undefined, "Client display bound"));
  const list = element("ul");
  for (const notice of notices) append(list, element("li", undefined, notice));
  append(box, list);
  return box;
}

export function vaultLink(vault: string): HTMLAnchorElement {
  return link(vault, routeHref({ name: "browse", vault }));
}

export function healthBadge(status: string): HTMLElement {
  const tone = status === "current" || status === "active" || status === "distilled" ? "good" :
    status === "failed" || status === "missing" || status === "unavailable" ? "bad" : "warn";
  return badge(status, tone);
}

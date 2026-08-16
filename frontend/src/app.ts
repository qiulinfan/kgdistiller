import type { ApiEnvelope, ApiResult, RecallRequest } from "./api/contracts";
import { ApiClient } from "./api/client";
import { ContractFailure } from "./api/errors";
import { append, badge, button, element, formatDigest, link } from "./components/dom";
import { applyTheme, loadTheme, type ThemePreference } from "./components/theme";
import { GenerationStore, type LoadedRoute } from "./state/generation";
import type { WorkspaceState } from "./state/model";
import { parseRoute, routeHref, type Route } from "./state/router";
import { renderBrowse } from "./views/browse";
import { renderConcept } from "./views/concept";
import { renderHealth } from "./views/health";
import { renderHome } from "./views/home";
import { renderReview } from "./views/review";
import { renderSearch } from "./views/search";
import { renderSource } from "./views/source";

const MAX_BROWSE_CALLS = 12;
const MAX_SECOND_HOP_CALLS = 4;

function recallRequest(
  operation: "search" | "context",
  options: { query?: string; handle?: string; vault?: string; scope?: string }
): RecallRequest {
  return {
    schema: "qlkg-recall-request-v1",
    operation,
    vault_ids: options.vault ? [options.vault] : [],
    queries: [],
    query: options.query ?? null,
    handle: null,
    handles: options.handle ? [options.handle] : [],
    scopes: options.scope ? [options.scope] : [],
    direction: "both",
    edge_types: [],
    max_depth: 1,
    limit: 50,
    token_budget: 6000,
    include_stale: false
  };
}

function compareText(left: string, right: string): number {
  return left < right ? -1 : left > right ? 1 : 0;
}

export class WorkbenchApp {
  readonly #root: HTMLElement;
  readonly #client: ApiClient;
  readonly #store: GenerationStore;
  #routeError: string | null = null;
  #theme: ThemePreference = loadTheme();

  constructor(root: HTMLElement, client = new ApiClient()) {
    this.#root = root;
    this.#client = client;
    this.#store = new GenerationStore(client);
  }

  start(): void {
    applyTheme(this.#theme);
    this.#store.subscribe((state) => this.#render(state));
    window.addEventListener("hashchange", () => this.#openHash());
    window.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLocaleLowerCase("en-US") === "k") {
        event.preventDefault();
        (document.querySelector("#global-search") as HTMLInputElement | null)?.focus();
      }
    });
    this.#openHash();
  }

  #openHash(): void {
    try {
      this.#routeError = null;
      const route = parseRoute(location.hash);
      void this.#store.navigate(route, this.#loader(route));
    } catch (error) {
      this.#routeError = error instanceof ContractFailure ? error.message : "The route is malformed.";
      history.replaceState(null, "", "#/home");
      void this.#store.navigate({ name: "home" });
    }
  }

  #go(route: Route): void {
    const href = routeHref(route);
    if (location.hash === href) void this.#store.navigate(route, this.#loader(route));
    else location.hash = href;
  }

  #loader(route: Route): ((generation: string, signal: AbortSignal) => Promise<ApiEnvelope<ApiResult> | LoadedRoute>) | undefined {
    switch (route.name) {
      case "home":
      case "health":
        return undefined;
      case "search":
        if (!route.query.trim()) return undefined;
        return (generation, signal) => this.#client.search(recallRequest("search", { query: route.query, vault: route.vault ?? undefined, scope: route.scope ?? undefined }), generation, signal);
      case "browse":
        if (route.vault === null) return undefined;
        return async (generation, signal) => {
          const primary = await this.#client.roots(route.vault as string, generation, { limit: 100, signal });
          const related: ApiEnvelope<ApiResult>[] = [];
          const queued = primary.result.nodes.map((node) => node.handle).sort(compareText);
          const seen = new Set(queued);
          while (queued.length && related.length < MAX_BROWSE_CALLS) {
            const handle = queued.shift();
            if (!handle) break;
            const row = await this.#client.neighbors(handle, generation, { direction: "outgoing", limit: 100, signal });
            related.push(row);
            const taxonomyTargets = new Set(row.result.edges.filter((edge) => edge.relation === "contains" && edge.source === handle).map((edge) => edge.target));
            for (const node of [...row.result.nodes].sort((left, right) => compareText(left.handle, right.handle))) {
              if (!seen.has(node.handle) && taxonomyTargets.has(node.handle) && (node.type === "field" || node.type === "topic")) {
                seen.add(node.handle);
                queued.push(node.handle);
              }
            }
            queued.sort(compareText);
          }
          return {
            primary,
            related,
            notices: queued.length ? ["Taxonomy expansion reached the 12-request client bound; the visible graph is partial."] : []
          };
        };
      case "node":
        return async (generation, signal) => {
          const [primary, first, context] = await Promise.all([
            this.#client.node(route.handle, generation, { signal }),
            this.#client.neighbors(route.handle, generation, { limit: 81, signal }),
            this.#client.context(recallRequest("context", { handle: route.handle }), generation, signal)
          ]);
          const related: ApiEnvelope<ApiResult>[] = [first, context];
          const candidates = first.result.nodes.filter((node) => node.handle !== route.handle).sort((left, right) => compareText(left.handle, right.handle));
          const next = candidates.slice(0, MAX_SECOND_HOP_CALLS);
          for (const node of next) related.push(await this.#client.neighbors(node.handle, generation, { limit: 81, signal }));
          return {
            primary,
            related,
            notices: candidates.length > MAX_SECOND_HOP_CALLS ? ["Second-hop expansion reached the 4-neighbor client bound; the visible neighborhood is partial."] : []
          };
        };
      case "source":
        return async (generation, signal) => {
          const primary = await this.#client.source(route.vault, route.document, generation, signal);
          const [versions, diff, excerpt] = await Promise.all([
            this.#client.versions(route.vault, route.document, generation, { limit: 20, signal }),
            this.#client.diff(route.vault, route.document, generation, { signal }),
            this.#client.excerpt(route.vault, route.document, generation, { line: 1, radius: 8, signal })
          ]);
          return { primary, related: [versions, diff, excerpt] };
        };
      case "review":
        if (route.vault === null) return undefined;
        return (generation, signal) => this.#client.stale(route.vault as string, generation, { limit: 100, cursor: route.cursor ?? undefined, signal });
    }
  }

  #render(state: WorkspaceState): void {
    const shell = element("div", "app-shell");
    const header = this.#header(state);
    const workspace = element("div", "workspace");
    const navigation = this.#navigation(state);
    const main = element("main", "main-panel");
    main.id = "main-content";
    main.tabIndex = -1;
    if (this.#routeError) main.append(element("div", "notice notice-bad", this.#routeError));
    if (state.phase === "contract-error" || state.phase === "unavailable" || state.phase === "stale-generation") {
      const notice = element("section", `notice ${state.phase === "contract-error" ? "notice-bad" : "notice-warn"}`);
      append(notice, element("h1", undefined, state.phase === "contract-error" ? "Data contract rejected" : "Workspace unavailable"), element("p", undefined, state.message ?? "The local service could not provide this view."));
      main.append(notice);
    }
    if (state.phase === "loading" && state.response === null) {
      const loading = element("p", "loading", "Loading a coherent generation…");
      loading.setAttribute("role", "status");
      main.append(loading);
    } else {
      main.append(this.#view(state));
      if (state.phase === "refreshing") main.prepend(element("p", "refreshing", "Refreshing this view…"));
      if (state.phase === "partial") main.prepend(element("p", "notice notice-warn", "Some registered Vaults are incomplete; visible data remains generation-coherent."));
    }
    const context = this.#contextPanel(state);
    append(workspace, navigation, main, context);
    append(shell, header, workspace);
    this.#root.replaceChildren(shell);
  }

  #header(state: WorkspaceState): HTMLElement {
    const header = element("header", "topbar");
    append(header, link("kgdistiller", "#/home", "brand"));
    const form = element("form", "global-search");
    form.setAttribute("role", "search");
    const input = element("input");
    input.id = "global-search";
    input.type = "search";
    input.maxLength = 4096;
    input.placeholder = "Search all Vaults  Ctrl K";
    input.setAttribute("aria-label", "Search all Vaults");
    form.addEventListener("submit", (event) => {
      event.preventDefault();
      if (input.value.trim()) this.#go({ name: "search", query: input.value.trim(), vault: null, scope: null });
    });
    append(form, input);
    const theme = element("select", "theme-select");
    theme.setAttribute("aria-label", "Color theme");
    for (const value of ["auto", "light", "dark"] as const) {
      const option = element("option", undefined, value === "auto" ? "Theme: auto" : `Theme: ${value}`);
      option.value = value;
      option.selected = value === this.#theme;
      theme.append(option);
    }
    theme.addEventListener("change", () => {
      this.#theme = theme.value as ThemePreference;
      applyTheme(this.#theme);
    });
    const generation = element("code", "top-generation", formatDigest(state.generation));
    generation.title = state.generation ?? "No captured generation";
    append(header, form, generation, theme);
    return header;
  }

  #navigation(state: WorkspaceState): HTMLElement {
    const nav = element("nav", "side-nav");
    nav.id = "workspace-navigation";
    nav.setAttribute("aria-label", "Workspace");
    const content = element("div", "nav-drawer-content");
    content.id = "workspace-navigation-content";
    const drawer = button("Workspace menu", () => {
      const expanded = nav.dataset.open !== "true";
      nav.dataset.open = String(expanded);
      drawer.setAttribute("aria-expanded", String(expanded));
    }, "drawer-toggle");
    drawer.setAttribute("aria-controls", content.id);
    drawer.setAttribute("aria-expanded", "false");
    const links: Array<[string, Route]> = [
      ["Home", { name: "home" }],
      ["Search", { name: "search", query: "", vault: null, scope: null }],
      ["Browse", { name: "browse", vault: state.route.name === "browse" ? state.route.vault : null }],
      ["Review", { name: "review", vault: state.route.name === "review" ? state.route.vault : null, cursor: null }],
      ["Health", { name: "health" }]
    ];
    const list = element("ul", "nav-list");
    for (const [label, route] of links) {
      const item = element("li");
      const anchor = link(label, routeHref(route), state.route.name === route.name ? "nav-link active" : "nav-link");
      if (state.route.name === route.name) anchor.setAttribute("aria-current", "page");
      item.append(anchor);
      list.append(item);
    }
    const selector = element("select", "vault-selector");
    selector.setAttribute("aria-label", "Active Vault");
    const all = element("option", undefined, "All Vaults");
    all.value = "";
    selector.append(all);
    const selected = "vault" in state.route ? state.route.vault : state.route.name === "node" ? state.route.handle.split(":", 1)[0] ?? null : null;
    for (const vault of state.vaults) {
      const option = element("option", undefined, vault.label);
      option.value = vault.vault_id;
      option.selected = selected === vault.vault_id;
      selector.append(option);
    }
    selector.addEventListener("change", () => {
      const vault = selector.value || null;
      this.#go(state.route.name === "review" ? { name: "review", vault, cursor: null } : { name: "browse", vault });
    });
    append(content, list, element("label", "selector-label", "Vault selector"), selector);
    append(nav, drawer, content);
    return nav;
  }

  #contextPanel(state: WorkspaceState): HTMLElement {
    const aside = element("aside", "context-panel");
    append(aside, element("h2", undefined, "Capture health"));
    if (state.status) append(aside, badge(`${state.status.healthy_vaults}/${state.status.registered_vaults} healthy`, state.status.incomplete_vaults ? "warn" : "good"));
    for (const item of state.incompleteVaults.slice(0, 8)) {
      const row = element("p", "health-row");
      append(row, badge(item.code, "bad"), document.createTextNode(` ${item.vault_id}`));
      aside.append(row);
    }
    if (state.incompleteVaults.length > 8) aside.append(element("p", "muted", `${state.incompleteVaults.length - 8} more incomplete Vaults`));
    append(aside, element("p", "read-only-note", "Read-only HTTP workspace. Knowledge writes stay transactional in the CLI and Skills."));
    return aside;
  }

  #view(state: WorkspaceState): HTMLElement {
    const context = { state, go: (route: Route) => this.#go(route) };
    switch (state.route.name) {
      case "home": return renderHome(context);
      case "search": return renderSearch(context);
      case "browse": return renderBrowse(context);
      case "node": return renderConcept(context);
      case "source": return renderSource(context);
      case "review": return renderReview(context);
      case "health": return renderHealth(context);
    }
  }
}

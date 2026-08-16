import { ContractFailure } from "../api/errors";

export type Route =
  | { name: "home" }
  | { name: "search"; query: string; vault: string | null; scope: string | null }
  | { name: "browse"; vault: string | null }
  | { name: "node"; handle: string }
  | { name: "source"; vault: string; document: string }
  | { name: "review"; vault: string | null; cursor: string | null }
  | { name: "health" };

const VAULT = /^[a-z0-9]+(?:[._-][a-z0-9]+)*$/u;
const HANDLE = /^[a-z0-9]+(?:[._-][a-z0-9]+)*:[a-z0-9]+(?:-[a-z0-9]+)*$/u;
const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/u;

function fields(query: URLSearchParams, allowed: Set<string>): void {
  const keys = [...query.keys()];
  if (keys.length > allowed.size || new Set(keys).size !== keys.length || keys.some((key) => !allowed.has(key))) {
    throw new ContractFailure("hash route contains unknown or duplicate fields");
  }
}

function bounded(query: URLSearchParams, key: string, maximum: number): string | null {
  const value = query.get(key);
  if (value !== null && value.length > maximum) throw new ContractFailure("hash route field exceeds its bound");
  return value;
}

function vault(query: URLSearchParams): string | null {
  const value = bounded(query, "vault", 64);
  if (value !== null && !VAULT.test(value)) throw new ContractFailure("hash route Vault is malformed");
  return value;
}

export function parseRoute(hash: string): Route {
  const value = hash || "#/home";
  if (!value.startsWith("#/") || value.includes("\\") || value.includes("\0")) {
    throw new ContractFailure("hash route is malformed");
  }
  const routeText = value.slice(1);
  const delimiter = routeText.indexOf("?");
  const rawPath = delimiter < 0 ? routeText : routeText.slice(0, delimiter);
  const rawQuery = delimiter < 0 ? "" : routeText.slice(delimiter + 1);
  if (!rawPath || rawPath.includes("//") || rawPath.split("/").some((part) => part === "." || part === "..")) {
    throw new ContractFailure("hash route is not canonical");
  }
  const query = new URLSearchParams(rawQuery);
  switch (rawPath) {
    case "/home":
      fields(query, new Set());
      return { name: "home" };
    case "/search": {
      fields(query, new Set(["q", "vault", "scope"]));
      const text = bounded(query, "q", 4096) ?? "";
      const scope = bounded(query, "scope", 321);
      if (scope !== null && !HANDLE.test(scope)) throw new ContractFailure("hash route scope is malformed");
      return { name: "search", query: text, vault: vault(query), scope };
    }
    case "/browse":
      fields(query, new Set(["vault"]));
      return { name: "browse", vault: vault(query) };
    case "/node": {
      fields(query, new Set(["handle"]));
      const handle = bounded(query, "handle", 321) ?? "";
      if (!HANDLE.test(handle)) throw new ContractFailure("hash route node handle is malformed");
      return { name: "node", handle };
    }
    case "/source": {
      fields(query, new Set(["vault", "document"]));
      const selectedVault = vault(query);
      const document = bounded(query, "document", 36) ?? "";
      if (selectedVault === null || !UUID.test(document)) throw new ContractFailure("hash route source identity is malformed");
      return { name: "source", vault: selectedVault, document };
    }
    case "/review":
      fields(query, new Set(["vault", "cursor"]));
      return { name: "review", vault: vault(query), cursor: bounded(query, "cursor", 4096) };
    case "/health":
      fields(query, new Set());
      return { name: "health" };
    default:
      throw new ContractFailure("hash route is unavailable");
  }
}

export function routeHref(route: Route): string {
  const query = new URLSearchParams();
  switch (route.name) {
    case "home": return "#/home";
    case "health": return "#/health";
    case "search":
      if (route.query) query.set("q", route.query);
      if (route.vault) query.set("vault", route.vault);
      if (route.scope) query.set("scope", route.scope);
      break;
    case "browse":
      if (route.vault) query.set("vault", route.vault);
      break;
    case "node": query.set("handle", route.handle); break;
    case "source":
      query.set("vault", route.vault);
      query.set("document", route.document);
      break;
    case "review":
      if (route.vault) query.set("vault", route.vault);
      if (route.cursor) query.set("cursor", route.cursor);
      break;
  }
  const encoded = query.toString();
  return `#/${route.name}${encoded ? `?${encoded}` : ""}`;
}

export function sameRoute(left: Route, right: Route): boolean {
  return routeHref(left) === routeHref(right);
}

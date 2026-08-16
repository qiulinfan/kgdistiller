export type Child = Node | string | null | undefined;

export function element<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  className?: string,
  text?: string
): HTMLElementTagNameMap[K] {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function append(parent: Node, ...children: Child[]): void {
  for (const child of children) {
    if (child === null || child === undefined) continue;
    parent.appendChild(typeof child === "string" ? document.createTextNode(child) : child);
  }
}

export function badge(text: string, tone: "neutral" | "good" | "warn" | "bad" = "neutral"): HTMLElement {
  const value = element("span", `badge badge-${tone}`, text);
  value.dir = "auto";
  return value;
}

export function button(text: string, action: () => void, className = "button"): HTMLButtonElement {
  const value = element("button", className, text);
  value.type = "button";
  value.addEventListener("click", action);
  return value;
}

export function link(text: string, href: string, className = "text-link"): HTMLAnchorElement {
  const value = element("a", className, text);
  value.href = href;
  value.dir = "auto";
  return value;
}

export function emptyState(title: string, detail: string): HTMLElement {
  const section = element("section", "empty-state");
  append(section, element("h2", undefined, title), element("p", undefined, detail));
  return section;
}

export function formatDigest(value: string | null): string {
  return value === null ? "none" : `${value.slice(0, 10)}…${value.slice(-6)}`;
}

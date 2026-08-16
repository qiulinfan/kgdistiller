import { afterEach, describe, expect, it, vi } from "vitest";
import { WorkbenchApp } from "../src/app";
import { ApiClient } from "../src/api/client";
import { envelope, vaultCard } from "./fixtures";

afterEach(() => {
  document.documentElement.removeAttribute("data-theme");
  localStorage.clear();
  document.body.replaceChildren();
  history.replaceState(null, "", "#/home");
});

describe("workbench shell", () => {
  it("provides landmarks, focus navigation, and persistent theme controls", async () => {
    const status = await envelope({
      kind: "status" as const,
      api_version: 1 as const,
      read_only: true as const,
      registered_vaults: 1,
      healthy_vaults: 1,
      incomplete_vaults: 0
    });
    const vaults = await envelope({ kind: "vaults" as const, vaults: status.vault_generations.map(vaultCard) });
    const client = {
      status: vi.fn(async () => status),
      vaults: vi.fn(async () => vaults),
      clearGeneration: vi.fn()
    } as unknown as ApiClient;
    document.body.innerHTML = '<a class="skip-link" href="#main-content">Skip to knowledge workspace</a><div id="app"></div>';
    history.replaceState(null, "", "#/review");
    const root = document.querySelector<HTMLElement>("#app");
    expect(root).not.toBeNull();
    if (!root) return;
    new WorkbenchApp(root, client).start();

    await vi.waitFor(() => expect(root.querySelector("main#main-content")).not.toBeNull());
    expect(document.querySelector('.skip-link[href="#main-content"]')).not.toBeNull();
    expect(root.querySelector("header")).not.toBeNull();
    expect(root.querySelector('nav[aria-label="Workspace"]')).not.toBeNull();
    expect(root.querySelector("aside")).not.toBeNull();
    const drawer = root.querySelector<HTMLButtonElement>('.drawer-toggle[aria-controls="workspace-navigation-content"]');
    expect(drawer?.getAttribute("aria-expanded")).toBe("false");
    drawer?.click();
    expect(drawer?.getAttribute("aria-expanded")).toBe("true");
    const search = root.querySelector<HTMLInputElement>("#global-search");
    window.dispatchEvent(new KeyboardEvent("keydown", { key: "k", ctrlKey: true, bubbles: true }));
    expect(document.activeElement).toBe(search);

    const theme = root.querySelector<HTMLSelectElement>('select[aria-label="Color theme"]');
    expect(theme).not.toBeNull();
    if (theme) {
      theme.value = "dark";
      theme.dispatchEvent(new Event("change", { bubbles: true }));
    }
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem("kgdistiller-theme-v1")).toBe("dark");

    await vi.waitFor(() => expect(root.querySelector('option[value="alpha"]')).not.toBeNull());
    const vault = root.querySelector<HTMLSelectElement>('select[aria-label="Active Vault"]');
    expect(vault).not.toBeNull();
    if (vault) {
      vault.value = "alpha";
      vault.dispatchEvent(new Event("change", { bubbles: true }));
    }
    expect(location.hash).toBe("#/review?vault=alpha");
  });
});

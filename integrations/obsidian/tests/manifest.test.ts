import { readFile } from "node:fs/promises";

import { describe, expect, it } from "vitest";

describe("Obsidian plugin metadata", () => {
  it("keeps package, manifest, and compatibility versions aligned", async () => {
    const readJson = async (path: string): Promise<Record<string, unknown>> =>
      JSON.parse(await readFile(path, "utf8")) as Record<string, unknown>;
    const packageJson = await readJson("package.json");
    const manifest = await readJson("manifest.json");
    const versions = await readJson("versions.json");
    expect(manifest.id).toBe("kgdistiller");
    expect(packageJson.version).toBe(manifest.version);
    expect(versions[manifest.version as string]).toBe(manifest.minAppVersion);
    expect(manifest.isDesktopOnly).toBe(false);
  });
});

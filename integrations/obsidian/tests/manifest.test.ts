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
    expect(manifest.minAppVersion).toBe("1.13.7");
    expect(await readJson("../../manifest.json")).toEqual(manifest);
    expect(await readJson("../../versions.json")).toEqual(versions);
    const rootPackage = await readJson("../../package.json");
    expect(rootPackage.version).toBe(manifest.version);
    expect(rootPackage.license).toBe("MIT-0");
    expect(packageJson.license).toBe("MIT-0");
  });
});

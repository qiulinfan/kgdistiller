#!/usr/bin/env node
// The nested integration remains canonical for the Python package's bundled installer.
import assert from "node:assert/strict";
import { spawnSync } from "node:child_process";
import { readFile, copyFile } from "node:fs/promises";
import { dirname, join, resolve } from "node:path";
import { fileURLToPath } from "node:url";

const root = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const integration = join(root, "integrations/obsidian");
const args = process.argv.slice(2);
const verifyOnly = args.includes("--verify-only");
const tagAt = args.indexOf("--tag");
const tag = tagAt < 0 ? null : args[tagAt + 1];
if (args.some((arg, i) => arg !== "--verify-only" && arg !== "--tag" && !(tagAt >= 0 && i === tagAt + 1))) throw new Error("Usage: node scripts/build-obsidian-plugin.mjs [--verify-only] [--tag x.y.z]");
const json = async path => JSON.parse(await readFile(path, "utf8"));
const manifest = await json(join(integration, "manifest.json"));
assert.deepEqual(await json(join(root, "manifest.json")), manifest, "Root manifest must match the canonical Obsidian integration");
const versions = await json(join(integration, "versions.json"));
assert.deepEqual(await json(join(root, "versions.json")), versions, "Root compatibility versions must match the integration");
assert.equal(versions[manifest.version], manifest.minAppVersion);
for (const folder of [root, integration]) {
  const metadata = await json(join(folder, "package.json"));
  assert.equal(metadata.version, manifest.version, "Plugin package/manifest versions differ");
  assert.equal(metadata.license, "MIT-0");
}
assert.match(manifest.version, /^\d+\.\d+\.\d+$/);
if (tagAt >= 0) assert.equal(tag, manifest.version, "Release tag must exactly match manifest.version (no v prefix)");
if (!verifyOnly) {
  const npm = process.platform === "win32" ? "npm.cmd" : "npm";
  // The directory's clean builder may set NODE_ENV=production. Build tools are
  // devDependencies, so include them explicitly without changing runtime deps.
  for (const command of [["ci", "--include=dev"], ["run", "build"]]) {
    const run = spawnSync(npm, command, { cwd: integration, stdio: "inherit", shell: process.platform === "win32" });
    if (run.error) throw run.error;
    if (run.status !== 0) process.exit(run.status ?? 1);
  }
  const bundle = await readFile(join(integration, "main.js"), "utf8");
  assert.ok(bundle.includes("Copyright (c) 2016-2026, The Cytoscape Consortium."));
  assert.ok(bundle.includes("Permission is hereby granted"));
  assert.ok(bundle.includes("The above copyright notice and this permission notice"));
  for (const name of ["main.js", "styles.css"]) await copyFile(join(integration, name), join(root, name));
}
console.log(`kgdistiller Obsidian ${manifest.version}: root metadata verified${verifyOnly ? "" : "; installable root assets built"}`);

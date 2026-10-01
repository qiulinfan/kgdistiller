import esbuild from "esbuild";
import process from "node:process";
import { builtinModules } from "node:module";
import { readFileSync } from "node:fs";

const production = process.argv[2] === "production";
const license = readFileSync(new URL("../../LICENSE", import.meta.url), "utf8");
const notices = readFileSync(new URL("../../THIRD_PARTY_NOTICES.md", import.meta.url), "utf8");
if (notices.includes("*/")) throw new Error("Third-party notices cannot terminate the bundle license comment");
const context = await esbuild.context({
  banner: {
    js: `/*! kgdistiller: SPDX-License-Identifier: MIT.\n${license}\n${notices}\n*/`,
  },
  entryPoints: ["src/main.ts"],
  bundle: true,
  external: [
    "obsidian",
    "electron",
    "@codemirror/autocomplete",
    "@codemirror/collab",
    "@codemirror/commands",
    "@codemirror/language",
    "@codemirror/lint",
    "@codemirror/search",
    "@codemirror/state",
    "@codemirror/view",
    "@lezer/common",
    "@lezer/highlight",
    "@lezer/lr",
    ...builtinModules,
  ],
  format: "cjs",
  target: "es2021",
  logLevel: "info",
  sourcemap: production ? false : "inline",
  treeShaking: true,
  outfile: "main.js",
  minify: production,
});

if (production) {
  await context.rebuild();
  await context.dispose();
} else {
  await context.watch();
}

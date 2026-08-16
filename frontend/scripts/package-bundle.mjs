import { createHash } from "node:crypto";
import { constants } from "node:fs";
import { access, lstat, mkdir, open, readdir, rename, rm, writeFile } from "node:fs/promises";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath, pathToFileURL } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const distRoot = resolve(frontendRoot, "dist");
const packageRoot = resolve(frontendRoot, "..", "src", "kgdistiller", "static", "v1");
const MAX_FILES = 128;
const MAX_FILE_BYTES = 4 * 1024 * 1024;
const MAX_BUNDLE_BYTES = 16 * 1024 * 1024;
const WINDOWS_RESERVED = new Set(["con", "prn", "aux", "nul", ...Array.from({ length: 9 }, (_, index) => `com${index + 1}`), ...Array.from({ length: 9 }, (_, index) => `lpt${index + 1}`)]);

function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value !== null && typeof value === "object") {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

function digest(data) {
  return createHash("sha256").update(data).digest("hex");
}

function portable(path) {
  if (!path || path.length > 256 || Buffer.byteLength(path, "utf8") > 4096 || path.normalize("NFC") !== path || path.includes("\\") || path.startsWith("/") || path.endsWith("/")) return false;
  const parts = path.split("/");
  return parts.every((part) => part && part !== "." && part !== ".." && !part.endsWith(" ") && !part.endsWith(".") && !/[<>:"|?*\u0000-\u001f\u007f]/u.test(part) && !WINDOWS_RESERVED.has((part.split(".", 1)[0] ?? "").toLowerCase()));
}

async function stableRead(path) {
  const handle = await open(path, constants.O_RDONLY);
  try {
    const before = await handle.stat();
    if (!before.isFile() || before.size < 1 || before.size > MAX_FILE_BYTES) throw new Error("frontend asset is not a bounded ordinary file");
    const data = await handle.readFile();
    const after = await handle.stat();
    if (before.size !== data.byteLength || after.size !== before.size || after.mtimeMs !== before.mtimeMs || after.ctimeMs !== before.ctimeMs) throw new Error("frontend asset changed during packaging");
    return data;
  } finally {
    await handle.close();
  }
}

async function inventory(root) {
  const files = [];
  const pending = [root];
  let entriesSeen = 0;
  while (pending.length) {
    const directory = pending.pop();
    if (!directory) break;
    const entries = await readdir(directory, { withFileTypes: true });
    if (entries.length > MAX_FILES + 4) throw new Error("frontend directory exceeds its entry bound");
    for (const entry of entries) {
      entriesSeen += 1;
      if (entriesSeen > MAX_FILES + 4) throw new Error("frontend build exceeds its aggregate entry bound");
      const absolute = join(directory, entry.name);
      const metadata = await lstat(absolute);
      if (metadata.isSymbolicLink()) throw new Error("frontend build contains a link");
      if (metadata.isDirectory()) pending.push(absolute);
      else if (metadata.isFile()) files.push(relative(root, absolute).split(sep).join("/"));
      else throw new Error("frontend build contains a non-file artifact");
      if (files.length > MAX_FILES) throw new Error("frontend build exceeds its file bound");
    }
  }
  files.sort();
  return files;
}

function media(path) {
  if (path === "index.html") return "text/html; charset=utf-8";
  if (path.endsWith(".js")) return "text/javascript; charset=utf-8";
  if (path.endsWith(".css")) return "text/css; charset=utf-8";
  throw new Error("frontend build contains an unsupported asset type");
}

export function assertOffline(path, data) {
  const text = new TextDecoder("utf-8", { fatal: true }).decode(data);
  const withoutSvgNamespace = text.replace(/(["'`])http:\/\/www\.w3\.org\/2000\/svg\1/gu, "$1__SVG_NAMESPACE__$1");
  const scanned = withoutSvgNamespace.replace(/\\u002f/giu, "/").replace(/\\x2f/giu, "/").replace(/\\\//gu, "/");
  const protocolAuthority = /["'`(=]\s*\/\/[^\s"'`<>{}]/u.test(scanned);
  const backslashAuthority = (path.endsWith(".js")
    ? /["'`(=]\s*\\{4,}[^\s"'`<>{}]/u
    : /["'`(=]\s*\\{2,}[^\s"'`<>{}]/u).test(scanned);
  if (/(?:https?|wss?|ftp):/iu.test(scanned) || protocolAuthority || backslashAuthority || /sourceMappingURL/iu.test(text)) {
    throw new Error(`frontend asset ${path} contains a remote URL or source map reference`);
  }
}

export function assertIndexReferences(indexText, paths) {
  const available = new Set(paths);
  const references = [...indexText.matchAll(/(?:src|href)="([^"]+)"/gu)].map((match) => match[1]);
  for (const reference of references) {
    if (reference?.startsWith("#")) continue;
    if (!reference?.startsWith("/") || !available.has(reference.slice(1))) {
      throw new Error("frontend index references an unavailable packaged asset");
    }
  }
}

async function buildBundle() {
  const paths = await inventory(distRoot);
  if (!paths.includes("index.html") || paths.some((path) => !portable(path) || path.endsWith(".map"))) throw new Error("frontend build inventory is not portable and closed");
  const foldedPaths = paths.map((path) => path.split("/").map((part) => part.normalize("NFC").toLowerCase()).join("/"));
  if (new Set(foldedPaths).size !== foldedPaths.length) throw new Error("frontend build paths collide on a portable filesystem");
  if (paths.some((path) => path !== "index.html" && !/^assets\/[A-Za-z0-9_-]+-[A-Za-z0-9_-]{8,}\.(?:js|css)$/u.test(path))) throw new Error("frontend asset is not fingerprinted");
  const contents = new Map();
  const files = [];
  let total = 0;
  for (const path of paths) {
    const data = await stableRead(join(distRoot, ...path.split("/")));
    assertOffline(path, data);
    total += data.byteLength;
    if (total > MAX_BUNDLE_BYTES) throw new Error("frontend bundle exceeds its aggregate byte bound");
    contents.set(path, data);
    files.push({
      path,
      media_type: media(path),
      bytes: data.byteLength,
      sha256: digest(data),
      cache_policy: path === "index.html" ? "no-store" : "immutable"
    });
  }
  const index = contents.get("index.html");
  if (!index) throw new Error("frontend index is unavailable");
  const indexText = new TextDecoder("utf-8", { fatal: true }).decode(index);
  const entries = [...indexText.matchAll(/<script\s+type="module"(?:\s+crossorigin)?\s+src="\/([^"?]+)"\s*><\/script>/gu)].map((match) => match[1]);
  if (entries.length !== 1 || !entries[0] || !paths.includes(entries[0]) || !entries[0].endsWith(".js")) throw new Error("frontend index does not bind one packaged entry module");
  assertIndexReferences(indexText, paths);
  const unsigned = {
    schema: "qlkg-frontend-bundle-v1",
    frontend_version: 1,
    api_version: 1,
    entry: entries[0],
    index: "index.html",
    files
  };
  const manifest = { ...unsigned, bundle_sha256: digest(Buffer.from(canonical(unsigned), "utf8")) };
  const manifestBytes = Buffer.from(`${canonical(manifest)}\n`, "utf8");
  if (total + manifestBytes.byteLength > MAX_BUNDLE_BYTES) throw new Error("frontend bundle manifest exceeds its aggregate byte bound");
  contents.set("bundle.json", manifestBytes);
  return { manifest, contents };
}

async function verifyAt(root, expected) {
  const actualPaths = await inventory(root);
  const expectedPaths = [...expected.contents.keys()].sort();
  if (actualPaths.join("\n") !== expectedPaths.join("\n")) throw new Error("packaged frontend inventory has drifted");
  for (const path of expectedPaths) {
    const actual = await stableRead(join(root, ...path.split("/")));
    if (!actual.equals(expected.contents.get(path))) throw new Error(`packaged frontend asset has drifted: ${path}`);
  }
}

async function verify(expected) {
  await verifyAt(packageRoot, expected);
}

async function write(expected) {
  const parent = dirname(packageRoot);
  const stage = `${packageRoot}.stage-${process.pid}`;
  const backup = `${packageRoot}.previous`;
  if (!packageRoot.endsWith(`${sep}src${sep}kgdistiller${sep}static${sep}v1`)) throw new Error("refusing an unexpected package target");
  await mkdir(parent, { recursive: true });
  await rm(stage, { recursive: true, force: true });
  await mkdir(stage, { recursive: false });
  try {
    for (const [path, data] of expected.contents) {
      const target = join(stage, ...path.split("/"));
      await mkdir(dirname(target), { recursive: true });
      await writeFile(target, data, { flag: "wx" });
    }
    await verifyAt(stage, expected);
    await rm(backup, { recursive: true, force: true });
    let hadPrevious = false;
    try {
      await rename(packageRoot, backup);
      hadPrevious = true;
    } catch (error) {
      if (error?.code !== "ENOENT") throw error;
    }
    try {
      await rename(stage, packageRoot);
    } catch (error) {
      if (hadPrevious) await rename(backup, packageRoot);
      throw error;
    }
    if (hadPrevious) await rm(backup, { recursive: true, force: true });
  } catch (error) {
    await rm(stage, { recursive: true, force: true });
    throw error;
  }
  await access(parent, constants.W_OK);
  await verify(expected);
}

const invokedPath = process.argv[1] ? pathToFileURL(resolve(process.argv[1])).href : null;
if (invokedPath === import.meta.url) {
  const mode = process.argv[2];
  if (mode !== "--write" && mode !== "--verify") throw new Error("usage: package-bundle.mjs --write|--verify");
  const expected = await buildBundle();
  if (mode === "--write") await write(expected);
  else await verify(expected);
}

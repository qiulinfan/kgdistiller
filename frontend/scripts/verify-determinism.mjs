import { spawnSync } from "node:child_process";
import { createHash } from "node:crypto";
import { cp, lstat, mkdtemp, readdir, readFile, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { dirname, join, relative, resolve, sep } from "node:path";
import { fileURLToPath } from "node:url";

const frontendRoot = resolve(dirname(fileURLToPath(import.meta.url)), "..");
const distRoot = join(frontendRoot, "dist");
const packageRoot = resolve(frontendRoot, "..", "src", "kgdistiller", "static", "v1");
const MAX_ENTRIES = 132;

function buildClean() {
  const npmCli = process.env.npm_execpath;
  if (!npmCli) throw new Error("npm did not expose its executable path");
  const result = spawnSync(process.execPath, [npmCli, "run", "build"], {
    cwd: frontendRoot,
    encoding: "utf8",
    stdio: "inherit",
    windowsHide: true
  });
  if (result.error) throw result.error;
  if (result.status !== 0) throw new Error("independent frontend build failed");
}

async function inventory(root) {
  const files = [];
  const pending = [root];
  let entries = 0;
  while (pending.length) {
    const directory = pending.pop();
    if (!directory) break;
    for (const entry of await readdir(directory, { withFileTypes: true })) {
      entries += 1;
      if (entries > MAX_ENTRIES) throw new Error("determinism inventory exceeded its bound");
      const absolute = join(directory, entry.name);
      const metadata = await lstat(absolute);
      if (metadata.isSymbolicLink()) throw new Error("determinism inventory contains a link");
      if (metadata.isDirectory()) pending.push(absolute);
      else if (metadata.isFile()) files.push(relative(root, absolute).split(sep).join("/"));
      else throw new Error("determinism inventory contains a special file");
    }
  }
  return files.sort();
}

async function assertTreeEqual(expectedRoot, actualRoot, label) {
  const expected = await inventory(expectedRoot);
  const actual = await inventory(actualRoot);
  if (expected.join("\n") !== actual.join("\n")) throw new Error(`${label} inventory is nondeterministic`);
  for (const path of expected) {
    const [left, right] = await Promise.all([
      readFile(join(expectedRoot, ...path.split("/"))),
      readFile(join(actualRoot, ...path.split("/")))
    ]);
    if (!left.equals(right)) throw new Error(`${label} bytes are nondeterministic: ${path}`);
  }
}

async function treeDigest(root) {
  const hash = createHash("sha256");
  for (const path of await inventory(root)) {
    const data = await readFile(join(root, ...path.split("/")));
    hash.update(Buffer.from(`${Buffer.byteLength(path, "utf8")}:${path}:${data.byteLength}:`, "utf8"));
    hash.update(data);
  }
  return hash.digest("hex");
}

const temporary = await mkdtemp(join(tmpdir(), "kgdistiller-f8-determinism-"));
try {
  await rm(distRoot, { recursive: true, force: true });
  buildClean();
  const firstDist = join(temporary, "dist");
  const firstPackage = join(temporary, "package");
  await cp(distRoot, firstDist, { recursive: true, errorOnExist: true, force: false });
  await cp(packageRoot, firstPackage, { recursive: true, errorOnExist: true, force: false });
  const firstDistDigest = await treeDigest(firstDist);
  const firstPackageDigest = await treeDigest(firstPackage);

  await rm(distRoot, { recursive: true, force: true });
  buildClean();
  await assertTreeEqual(firstDist, distRoot, "Vite output");
  await assertTreeEqual(firstPackage, packageRoot, "packaged bundle");
  const secondDistDigest = await treeDigest(distRoot);
  const secondPackageDigest = await treeDigest(packageRoot);
  process.stdout.write(
    `Two clean frontend builds are byte-identical: ${firstDistDigest} == ${secondDistDigest}\n` +
    `Two packaged bundles are byte-identical: ${firstPackageDigest} == ${secondPackageDigest}\n`
  );
} finally {
  await rm(temporary, { recursive: true, force: true });
}

// Generic tool registration originates in qiulinfan/kgdistiller-experiment,
// commit cf2960559f520cade5ce00cf1734705bf226dd01. Inputs stay caller-supplied.
import { readFile } from "node:fs/promises";
import { isAbsolute, join } from "node:path";
import { spawn } from "node:child_process";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

export default async function (pi: ExtensionAPI) {
  const run = process.cwd();
  const configPath = join(run, "compiled-tools-config.json");
  const config = JSON.parse(await readFile(configPath, "utf8"));
  const keys = ["python_interpreter", "library_path", "byte_budget", "reference_limit", "search_limit", "max_response_bytes"];
  if (!config || typeof config !== "object" || Object.keys(config).length !== keys.length ||
      keys.some(key => !Object.hasOwn(config, key)) ||
      ["python_interpreter", "library_path"].some(key => typeof config[key] !== "string" || !isAbsolute(config[key])) ||
      keys.slice(2).some(key => !Number.isSafeInteger(config[key]) || config[key] < 1)) {
    throw new Error("Invalid run-local compiled-tools config");
  }
  const questions = JSON.parse(await readFile(join(run, "questions.json"), "utf8"));
  if (!Array.isArray(questions) || !questions.length ||
      questions.some(row => !row || Object.keys(row).length !== 2 ||
        typeof row.qid !== "string" || !row.qid || typeof row.question !== "string" || !row.question.trim()) ||
      new Set(questions.map(row => row.qid)).size !== questions.length) {
    throw new Error("Invalid current question addresses");
  }
  const call = (request: object, signal?: AbortSignal) => new Promise<any>((resolve, reject) => {
    if (signal?.aborted) return reject(new Error("Compiled knowledge call cancelled"));
    let child;
    try {
      child = spawn(config.python_interpreter, ["-m", "kgdistiller.omp_compiled_tools", "--config", configPath, "--output", run],
        { cwd: run, shell: false, stdio: ["pipe", "pipe", "pipe"] });
    } catch {
      return reject(new Error("Configured bridge could not start"));
    }
    let size = 0, rejected = false;
    const chunks: Buffer[] = [];
    const cancel = () => { rejected = true; child.kill(); reject(new Error("Compiled knowledge call cancelled")); };
    signal?.addEventListener("abort", cancel, { once: true });
    child.stdout.on("data", chunk => {
      size += chunk.length;
      if (size > config.max_response_bytes + 1) {
        rejected = true; child.kill(); reject(new Error("Whole bridge response exceeds the configured limit"));
      } else chunks.push(chunk);
    });
    child.stderr.on("data", () => {}); // No machine paths or interpreter tracebacks reach the model.
    child.on("error", () => { rejected = true; reject(new Error("Configured bridge could not start")); });
    child.on("close", code => {
      signal?.removeEventListener("abort", cancel);
      if (rejected) return;
      if (code !== 0) return reject(new Error("Configured bridge process failed"));
      try { resolve(JSON.parse(Buffer.concat(chunks).toString("utf8"))); }
      catch { reject(new Error("Bridge returned an invalid complete JSON response")); }
    });
    child.stdin.on("error", () => {});
    child.stdin.end(JSON.stringify(request));
  });
  const packPreviews = new Set<string>();
  const register = (name: string, description: string, parameters: any, operation: string, approval: "read" | "write") => {
    pi.registerTool({ name, label: name, description, parameters, loadMode: "essential", approval, strict: true,
      async execute(_id, params, signal) {
        try {
          if (operation === "submit_selection" && params.selections.some((row: any) => !packPreviews.has(JSON.stringify(row.ranked)))) {
            throw new Error("Each ordered ranked list requires an earlier successful kgd_pack preview delivering every selected reference");
          }
          const previewKey = operation === "pack" ? JSON.stringify(params.references) : undefined;
          const response = await call({ ...params, operation }, signal);
          if (response.ok === true && previewKey !== undefined && Array.isArray(response.result?.entries) &&
              JSON.stringify(response.result.entries.map((entry: any) => entry?.reference)) === previewKey) {
            packPreviews.add(previewKey);
          }
          return { content: [{ type: "text", text: JSON.stringify(response) }],
            details: { ok: response.ok === true }, isError: response.ok !== true };
        } catch (error) {
          return { content: [{ type: "text", text: error instanceof Error ? error.message : "Compiled knowledge operation failed" }],
            details: { ok: false }, isError: true };
        }
      },
    });
  };
  const t = pi.arktype;
  const refs = t("string[]").atMostLength(config.reference_limit);
  register("kgd_search", "Find candidates using compiled definitions and authored retrieval expressions. Scores do not resolve identity.",
    t({ "+": "reject", query: "string", "limit?": t("number.integer").atLeast(1).atMost(config.search_limit) }), "search", "read");
  register("kgd_get", "Read whole entries by exact references, including definitions, conditions, evidence and explicit navigation. No source or relation is inferred.",
    t({ "+": "reject", references: refs }), "get", "read");
  register("kgd_inventory", "Enumerate the complete explicitly authored term/sense/use inventory, without topK. This is not a corpus completeness or equivalence certificate.",
    t({ "+": "reject", term: "string" }), "inventory", "read");
  register("kgd_pack", "Build a whole-entry scientific packet using this run's fixed byte budget. Omissions are explicit gaps, not task-success judgments.",
    t({ "+": "reject", references: refs }), "pack", "read");
  const selection = t({ "+": "reject", qid: t.enumerated(...questions.map(row => row.qid)), ranked: refs, abstain: "boolean" });
  register("submit_selection", "Submit exact existing references for every current question in order. Each ordered ranked list needs an earlier successful kgd_pack preview delivering all selected references, including an empty list for abstention. One immutable submission.",
    t({ "+": "reject", selections: selection.array() }), "submit_selection", "write");
}

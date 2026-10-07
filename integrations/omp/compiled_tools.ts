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
  const observations = new Map<string, { searched: boolean; candidates: Set<string>; overview: boolean; branches: Set<string> }>(
    questions.map(row => [row.qid, { searched: false, candidates: new Set<string>(), overview: false, branches: new Set<string>() }]),
  );
  const questionAddress = (params: any): string => {
    const qid = Object.hasOwn(params, "qid") ? params.qid : questions.length === 1 ? questions[0].qid : undefined;
    if (typeof qid !== "string" || !observations.has(qid)) {
      throw new Error("Search and browse require a current qid; it is optional only for a single question");
    }
    return qid;
  };
  const register = (name: string, description: string, parameters: any, operation: string, approval: "read" | "write") => {
    pi.registerTool({ name, label: name, description, parameters, loadMode: "essential", approval, strict: true,
      async execute(_id, params, signal) {
        try {
          if (operation === "submit_selection" && params.selections.some((row: any) => !packPreviews.has(JSON.stringify(row.ranked)))) {
            throw new Error("Each ordered ranked list requires an earlier successful kgd_pack preview delivering every selected reference");
          }
          if (operation === "submit_selection") {
            for (const row of params.selections) {
              const seen = observations.get(row.qid);
              if (!seen?.searched) throw new Error("Each question requires an earlier successful kgd_search");
              if (row.abstain) {
                if (!seen.overview) throw new Error("Abstention requires an earlier successful kgd_browse root overview for that question");
              } else {
                if (!row.ranked.some((reference: string) => seen.candidates.has(reference))) {
                  throw new Error("At least one selected reference must have been returned by that question's successful kgd_search");
                }
                if (!row.ranked.every((reference: string) => seen.branches.has(reference))) {
                  throw new Error("Every selected reference requires an earlier successful source, layer or term kgd_browse branch for that question");
                }
              }
            }
          }
          const qid = operation === "search" || operation === "browse" ? questionAddress(params) : undefined;
          const previewKey = operation === "pack" ? JSON.stringify(params.references) : undefined;
          const response = await call({ ...params, ...(qid === undefined ? {} : { qid }), operation }, signal);
          if (response.ok === true && qid !== undefined) {
            const seen = observations.get(qid)!;
            if (operation === "search" && Array.isArray(response.result)) {
              seen.searched = true;
              for (const row of response.result) if (typeof row?.reference === "string") seen.candidates.add(row.reference);
            } else if (operation === "browse") {
              if (params.reference === undefined && ["sources", "layers", "terms"].every(key => Array.isArray(response.result?.[key]))) {
                seen.overview = true;
              } else if (params.reference !== undefined && params.kind !== "node") {
                const rows = response.result?.entries ?? response.result?.senses;
                if (Array.isArray(rows)) {
                  for (const row of rows) if (typeof row?.reference === "string" && row.available !== false) seen.branches.add(row.reference);
                }
              }
            }
          }
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
  const address = { [questions.length === 1 ? "qid?" : "qid"]: t.enumerated(...questions.map(row => row.qid)) };
  register("kgd_search", "Find candidates for qid using compiled definitions and authored retrieval expressions. Before submission, at least one selected reference must be returned by this question's search. Scores do not resolve identity; qid only routes observations.",
    t({ "+": "reject", ...address, query: "string", "limit?": t("number.integer").atLeast(1).atMost(config.search_limit) }), "search", "read");
  register("kgd_browse", "Browse the source, layer and term tree for qid. Omit reference for the root overview and its navigation handles. Before submission, every selected reference must appear in a source/layer branch's entries or term branch's senses for this question. Node reads and root overview do not satisfy this branch prerequisite. Abstention requires a root overview.",
    t({ "+": "reject", ...address, "reference?": "string", "kind?": t.enumerated("source", "layer", "term", "node") }), "browse", "read");
  register("kgd_get", "Read whole entries by exact references, including definitions, conditions, evidence and explicit navigation. No source or relation is inferred.",
    t({ "+": "reject", references: refs }), "get", "read");
  register("kgd_inventory", "Enumerate the complete explicitly authored term/sense/use inventory, without topK. This is not a corpus completeness or equivalence certificate.",
    t({ "+": "reject", term: "string" }), "inventory", "read");
  register("kgd_pack", "Build a whole-entry scientific packet using this run's fixed byte budget. Omissions are explicit gaps, not task-success judgments.",
    t({ "+": "reject", references: refs }), "pack", "read");
  const selection = t({ "+": "reject", qid: t.enumerated(...questions.map(row => row.qid)), ranked: refs, abstain: "boolean" });
  register("submit_selection", "Submit exact existing references for every current question in order. Each question needs a successful search; at least one selected reference must be a returned candidate and every selected reference must have appeared in a source/layer/term tree branch for that question. Abstention needs a root overview. Each ordered ranked list also needs a successful complete kgd_pack preview, including an empty list for abstention. These observations do not certify semantic completeness. One immutable submission.",
    t({ "+": "reject", selections: selection.array() }), "submit_selection", "write");
}

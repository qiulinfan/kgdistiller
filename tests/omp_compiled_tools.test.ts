import { afterAll, describe, expect, mock, test } from "bun:test";
import { EventEmitter } from "node:events";
import { access, mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";

type Schema = {
  declaration: unknown;
  bounds: Record<string, number>;
  atMostLength: (limit: number) => Schema;
  atLeast: (limit: number) => Schema;
  atMost: (limit: number) => Schema;
  array: () => Schema;
};

// Record the injected schema declarations; validation is independently tested
// through the Python bridge, rather than reimplementing OMP's schema library.
function schema(declaration: unknown, bounds: Record<string, number> = {}): Schema {
  return {
    declaration,
    bounds,
    atMostLength: limit => schema(declaration, { ...bounds, atMostLength: limit }),
    atLeast: limit => schema(declaration, { ...bounds, atLeast: limit }),
    atMost: limit => schema(declaration, { ...bounds, atMost: limit }),
    array: () => schema({ array: schema(declaration, bounds) }),
  };
}

const arktype = Object.assign(schema, {
  enumerated: (...values: unknown[]) => schema({ enumerated: values }),
});

type Tool = {
  name: string;
  label: string;
  parameters: Schema;
  approval: string;
  loadMode: string;
  strict: boolean;
  execute: (id: string, params: object, signal?: AbortSignal) => Promise<{
    content: { type: string; text: string }[];
    details: { ok: boolean };
    isError: boolean;
  }>;
};

class FakeChild extends EventEmitter {
  stdout = new EventEmitter();
  stderr = new EventEmitter();
  stdin = Object.assign(new EventEmitter(), {
    end: (input: string) => { this.input = input; },
  });
  input = "";
  killed = false;
  kill() { this.killed = true; return true; }
}

type SpawnCall = { command: string; args: string[]; options: object; child: FakeChild };
const spawned: SpawnCall[] = [];
function successfulBridge(child: FakeChild) {
  queueMicrotask(async () => {
    const request = JSON.parse(child.input);
    if (request.operation === "submit_selection") {
      await writeFile("submitted-selection.json", JSON.stringify(request.selections), { flag: "wx" });
    }
    const members = ["meaning-a", "condition-b", "meaning-c"].map(reference => ({ reference }));
    let result: unknown = {};
    if (request.operation === "pack") result = { entries: request.references.map((reference: string) => ({ reference })), gaps: [] };
    if (request.operation === "search") result = request.query === "no matches" ? [] : request.query === "only c" ? [members[2]] : members;
    if (request.operation === "inventory") result = { groups: [{ senses: members }], uses: members };
    if (request.operation === "browse") {
      result = request.reference === undefined ? { sources: [{ reference: "source-a" }], layers: [{ reference: "definitions" }], terms: [{ reference: "meanings" }] }
        : request.kind === "node" || request.reference === "meaning-a" ? { reference: request.reference, statement: "Invented meaning" }
        : request.reference === "meanings" ? { reference: request.reference, senses: members }
        : { reference: request.reference, entries: request.reference === "unrelated" ? [{ reference: "other" }]
          : request.reference === "source-a" ? members.slice(0, 2) : members };
    }
    child.stdout.emit("data", Buffer.from(JSON.stringify({ ok: true, result }) + "\n"));
    child.emit("close", 0);
  });
}
let launch: (child: FakeChild) => void = successfulBridge;
const spawn = mock((command: string, args: string[], options: object) => {
  const child = new FakeChild();
  spawned.push({ command, args, options, child });
  launch(child);
  return child;
});
mock.module("node:child_process", () => ({ spawn }));
const activeScope = {};
let spillKilobytes: unknown = 50;
let spillSettingAvailable = true;
let activeScopeAvailable = true;
const getSpillSetting = mock((scope: unknown) => {
  expect(scope).toBe(activeScope);
  return spillKilobytes;
});
mock.module("@oh-my-pi/pi-coding-agent/config/registry", () => ({
  lookup: (id: string) => {
    expect(id).toBe("tools.artifactSpillThreshold");
    return spillSettingAvailable ? { get: getSpillSetting } : undefined;
  },
}));
mock.module("@oh-my-pi/pi-coding-agent/config/settings", () => ({
  findScopedSettings: () => activeScopeAvailable ? activeScope : undefined,
}));

const source = await readFile(new URL("../integrations/omp/compiled_tools.ts", import.meta.url), "utf8");
const javascript = new Bun.Transpiler({ loader: "ts", target: "bun" }).transformSync(source);
const registerTools = await (async () => {
  const directory = await mkdtemp(join(tmpdir(), "kgdistiller-omp-test-module-"));
  try {
    const path = join(directory, "compiled-tools.mjs");
    await writeFile(path, javascript);
    return (await import(path)).default;
  } finally {
    await rm(directory, { recursive: true, force: true });
  }
})();

afterAll(() => mock.restore());

const questions = [
  { qid: "first", question: "Which explicitly declared meaning applies?" },
  { qid: "second", question: "哪些条件必须保留？" },
];

async function withRun(
  action: (run: string, config: Record<string, unknown>, tools: Tool[]) => Promise<void>,
  options: { config?: Record<string, unknown>; questions?: unknown } = {},
) {
  const run = await mkdtemp(join(tmpdir(), "kgdistiller-omp-source-test-"));
  const previousCwd = process.cwd();
  const config = {
    python_interpreter: join(run, "lexical-venv", "bin", "python"),
    library_path: join(run, "caller-library.json"),
    byte_budget: 4096,
    reference_limit: 7,
    search_limit: 3,
    max_response_bytes: 24000,
    ...options.config,
  };
  const tools: Tool[] = [];
  spawned.length = 0;
  spawn.mockClear();
  launch = successfulBridge;
  spillKilobytes = 50;
  spillSettingAvailable = true;
  activeScopeAvailable = true;
  getSpillSetting.mockClear();
  try {
    await writeFile(join(run, "compiled-tools-config.json"), JSON.stringify(config));
    await writeFile(join(run, "questions.json"), JSON.stringify(options.questions ?? questions));
    process.chdir(run);
    await action(process.cwd(), config, tools);
  } finally {
    process.chdir(previousCwd);
    await rm(run, { recursive: true, force: true });
  }
}

async function registered(tools: Tool[]) {
  await registerTools({ arktype, registerTool: (tool: Tool) => tools.push(tool) });
}

function toolNamed(tools: Tool[], name: string): Tool {
  const tool = tools.find(tool => tool.name === name);
  if (!tool) throw new Error(`Test tool absent: ${name}`);
  return tool;
}

async function observe(tools: Tool[], qid: string, reference?: string, kind?: string) {
  expect((await toolNamed(tools, "kgd_search").execute("search", { qid, query: "Invented definitions" })).isError).toBe(false);
  expect((await toolNamed(tools, "kgd_browse").execute("browse", { qid, ...(reference === undefined ? {} : { reference, kind }) })).isError).toBe(false);
}

describe("OMP compiled tools source registration", () => {
  test("erases the SDK type import and registers exactly six tools with declared approvals", async () => {
    expect(javascript).not.toContain('from "@oh-my-pi/pi-coding-agent"');
    expect(javascript).toContain('from "@oh-my-pi/pi-coding-agent/config/registry"');
    expect(javascript).toContain('from "@oh-my-pi/pi-coding-agent/config/settings"');
    await withRun(async (_run, config, tools) => {
      await registered(tools);
      expect(tools.map(tool => [tool.name, tool.approval])).toEqual([
        ["kgd_search", "read"], ["kgd_browse", "read"], ["kgd_get", "read"], ["kgd_inventory", "read"],
        ["kgd_pack", "read"], ["submit_selection", "write"],
      ]);
      for (const tool of tools) {
        expect(tool.label).toBe(tool.name);
        expect(tool.loadMode).toBe("essential");
        expect(tool.strict).toBe(true);
        expect((tool.parameters.declaration as Record<string, unknown>)["+"]).toBe("reject");
      }
      const search = toolNamed(tools, "kgd_search").parameters.declaration as { "limit?": Schema };
      expect(search["limit?"].declaration).toBe("number.integer");
      expect(search["limit?"].bounds).toEqual({ atLeast: 1, atMost: config.search_limit });
      const browse = toolNamed(tools, "kgd_browse").parameters.declaration as Record<string, Schema>;
      expect(browse.qid.declaration).toEqual({ enumerated: questions.map(row => row.qid) });
      expect(browse["reference?"]).toBe("string");
      expect(browse["kind?"].declaration).toEqual({ enumerated: ["source", "layer", "term", "node"] });
      const pack = toolNamed(tools, "kgd_pack").parameters.declaration as { references: Schema };
      expect(Object.keys(pack).sort()).toEqual(["+", "references"]);
      expect(pack.references.declaration).toBe("string[]");
      expect(pack.references.bounds).toEqual({ atMostLength: config.reference_limit });
      const submission = toolNamed(tools, "submit_selection").parameters.declaration as { selections: Schema };
      const selection = submission.selections.declaration as { array: Schema };
      const fields = selection.array.declaration as { qid: Schema; ranked: Schema; abstain: string; "+": string };
      expect(fields.qid.declaration).toEqual({ enumerated: questions.map(row => row.qid) });
      expect(fields.ranked.bounds).toEqual({ atMostLength: config.reference_limit });
      expect(fields.abstain).toBe("boolean");
      expect(fields["+"]).toBe("reject");
      expect(spawn).not.toHaveBeenCalled();
    });
  });

  test("preserves the lexical interpreter and launches the installed bridge without a shell", async () => {
    await withRun(async (run, config, tools) => {
      await registered(tools);
      const result = await toolNamed(tools, "kgd_search").execute("call", { qid: "first", query: "有界映射", limit: 2 });
      expect(result.isError).toBe(false);
      expect(spawned).toHaveLength(1);
      expect(spawned[0].command).toBe(config.python_interpreter);
      expect(spawned[0].args).toEqual([
        "-m", "kgdistiller.omp_compiled_tools", "--config", join(run, "compiled-tools-config.json"), "--output", run,
      ]);
      expect(spawned[0].options).toEqual({ cwd: run, shell: false, stdio: ["pipe", "pipe", "pipe"] });
      expect(JSON.parse(spawned[0].child.input)).toEqual({ qid: "first", query: "有界映射", limit: 2, operation: "search" });
    });
  });

  test("routes each declared operation and keeps pack budget run-local", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      await observe(tools, "first", "definitions", "layer");
      await observe(tools, "second");
      const calls = [
        ["kgd_browse", { qid: "first", reference: "source-a", kind: "source" }, "browse"],
        ["kgd_get", { references: ["meaning-a", "condition-b"] }, "get"],
        ["kgd_inventory", { term: "有界映射" }, "inventory"],
        ["kgd_pack", { references: ["meaning-a"] }, "pack"],
        ["kgd_pack", { references: [] }, "pack"],
        ["submit_selection", { selections: [
          { qid: "first", ranked: ["meaning-a"], abstain: false },
          { qid: "second", ranked: [], abstain: true },
        ] }, "submit_selection"],
      ] as const;
      for (const [name, params, operation] of calls) {
        await toolNamed(tools, name).execute("call", params);
        expect(JSON.parse(spawned.at(-1)!.child.input)).toEqual({ ...params, operation });
      }
      expect(JSON.parse(spawned.find(call => JSON.parse(call.child.input).operation === "pack")!.child.input)).not.toHaveProperty("byte_budget");
    });
  });

  test("requires a valid explicit qid for multiple questions before starting retrieval", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      for (const [name, fields] of [["kgd_search", { query: "Invented definition" }], ["kgd_browse", {}]] as const) {
        for (const params of [fields, { ...fields, qid: "unknown" }, { ...fields, qid: null }]) {
          const result = await toolNamed(tools, name).execute("call", params);
          expect(result.isError).toBe(true);
          expect(result.content[0].text).toContain("current qid");
        }
      }
      expect(spawn).not.toHaveBeenCalled();
    });
  });

  test("resolves omitted qid only for a single question and declares it optional", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      for (const [name, fields] of [["kgd_search", { query: "Invented definition" }], ["kgd_browse", {}]] as const) {
        const declaration = toolNamed(tools, name).parameters.declaration as Record<string, Schema>;
        expect(declaration["qid?"].declaration).toEqual({ enumerated: ["first"] });
        expect(declaration.qid).toBeUndefined();
        expect((await toolNamed(tools, name).execute("call", fields)).isError).toBe(false);
        expect(JSON.parse(spawned.at(-1)!.child.input).qid).toBe("first");
      }
    }, { questions: questions.slice(0, 1) });
  });

  test("a branch and complete pack cannot replace a successful search", async () => {
    await withRun(async (run, _config, tools) => {
      await registered(tools);
      await toolNamed(tools, "kgd_browse").execute("branch", { reference: "source-a", kind: "source" });
      await toolNamed(tools, "kgd_pack").execute("preview", { references: ["meaning-a"] });
      const calls = spawned.length;
      const result = await toolNamed(tools, "submit_selection").execute("submit", { selections: [{ qid: "first", ranked: ["meaning-a"], abstain: false }] });
      expect(result.isError).toBe(true);
      expect(result.content[0].text).toContain("successful kgd_search");
      expect(spawned).toHaveLength(calls);
      await expect(access(join(run, "submitted-selection.json"))).rejects.toThrow();
    }, { questions: questions.slice(0, 1) });
  });

  test("a selection needs an actual returned search candidate, even after an empty successful search", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      await toolNamed(tools, "kgd_search").execute("search", { query: "only c" });
      await toolNamed(tools, "kgd_search").execute("empty", { query: "no matches" });
      await toolNamed(tools, "kgd_browse").execute("branch", { reference: "source-a", kind: "source" });
      await toolNamed(tools, "kgd_pack").execute("preview", { references: ["meaning-a"] });
      const result = await toolNamed(tools, "submit_selection").execute("submit", { selections: [{ qid: "first", ranked: ["meaning-a"], abstain: false }] });
      expect(result.isError).toBe(true);
      expect(result.content[0].text).toContain("returned by that question's successful kgd_search");
    }, { questions: questions.slice(0, 1) });
  });

  test("root overview, get, inventory and node browse do not qualify selected branch observations", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      await toolNamed(tools, "kgd_search").execute("search", { query: "Invented definition" });
      await toolNamed(tools, "kgd_pack").execute("preview", { references: ["meaning-a"] });
      const submit = () => toolNamed(tools, "submit_selection").execute("submit", { selections: [{ qid: "first", ranked: ["meaning-a"], abstain: false }] });
      expect((await submit()).isError).toBe(true);
      await toolNamed(tools, "kgd_browse").execute("root", {});
      await toolNamed(tools, "kgd_get").execute("get", { references: ["meaning-a"] });
      await toolNamed(tools, "kgd_inventory").execute("inventory", { term: "Invented meanings" });
      for (const params of [{ reference: "meaning-a" }, { reference: "meaning-a", kind: "node" }]) {
        await toolNamed(tools, "kgd_browse").execute("node", params);
        const result = await submit();
        expect(result.isError).toBe(true);
        expect(result.content[0].text).toContain("source, layer or term kgd_browse branch");
      }
    }, { questions: questions.slice(0, 1) });
  });

  test("unrelated branches and a branch missing any selected reference do not qualify", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      await observe(tools, "first", "unrelated", "source");
      await toolNamed(tools, "kgd_pack").execute("preview", { references: ["meaning-a", "meaning-c"] });
      const submit = () => toolNamed(tools, "submit_selection").execute("submit", { selections: [{ qid: "first", ranked: ["meaning-a", "meaning-c"], abstain: false }] });
      expect((await submit()).isError).toBe(true);
      await toolNamed(tools, "kgd_browse").execute("partial", { reference: "source-a", kind: "source" });
      expect((await submit()).isError).toBe(true);
    }, { questions: questions.slice(0, 1) });
  });

  test("successful source, layer or term branches qualify every selected reference", async () => {
    for (const [reference, kind] of [["source-a", "source"], ["definitions", "layer"], ["meanings", "term"], ["source-a", undefined]]) {
      await withRun(async (_run, _config, tools) => {
        await registered(tools);
        await observe(tools, "first", reference, kind);
        await toolNamed(tools, "kgd_pack").execute("preview", { references: ["meaning-a", "condition-b"] });
        const result = await toolNamed(tools, "submit_selection").execute("submit", { selections: [{ qid: "first", ranked: ["meaning-a", "condition-b"], abstain: false }] });
        expect(result.isError).toBe(false);
      }, { questions: questions.slice(0, 1) });
    }
  });

  test("tree expansion may add selections beyond search when at least one selected candidate was returned", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      await toolNamed(tools, "kgd_search").execute("search", { query: "only c" });
      await toolNamed(tools, "kgd_browse").execute("branch", { reference: "definitions", kind: "layer" });
      await toolNamed(tools, "kgd_pack").execute("preview", { references: ["meaning-c", "meaning-a"] });
      const result = await toolNamed(tools, "submit_selection").execute("submit", { selections: [{ qid: "first", ranked: ["meaning-c", "meaning-a"], abstain: false }] });
      expect(result.isError).toBe(false);
    }, { questions: questions.slice(0, 1) });
  });

  test("failed or oversized browse responses cannot qualify a branch", async () => {
    for (const oversized of [false, true]) {
      await withRun(async (run, _config, tools) => {
        await registered(tools);
        await toolNamed(tools, "kgd_search").execute("search", { query: "Invented definition" });
        await toolNamed(tools, "kgd_pack").execute("preview", { references: ["meaning-a"] });
        const response = oversized ? { ok: true, result: { entries: [{ reference: "meaning-a" }], padding: "x".repeat(1000) } }
          : { ok: false, error: { code: "invalid_browse", message: "Unknown branch" } };
        launch = child => queueMicrotask(() => {
          child.stdout.emit("data", Buffer.from(JSON.stringify(response) + "\n"));
          child.emit("close", 0);
        });
        expect((await toolNamed(tools, "kgd_browse").execute("failed", { reference: "source-a", kind: "source" })).isError).toBe(true);
        launch = successfulBridge;
        const calls = spawned.length;
        const result = await toolNamed(tools, "submit_selection").execute("submit", { selections: [{ qid: "first", ranked: ["meaning-a"], abstain: false }] });
        expect(result.isError).toBe(true);
        expect(spawned).toHaveLength(calls);
        await expect(access(join(run, "submitted-selection.json"))).rejects.toThrow();
      }, { questions: questions.slice(0, 1), config: { max_response_bytes: 512 } });
    }
  });

  test("failed or oversized searches cannot qualify search observations", async () => {
    for (const oversized of [false, true]) {
      await withRun(async (_run, _config, tools) => {
        await registered(tools);
        await toolNamed(tools, "kgd_browse").execute("branch", { reference: "source-a", kind: "source" });
        await toolNamed(tools, "kgd_pack").execute("preview", { references: ["meaning-a"] });
        const response = oversized ? { ok: true, result: [{ reference: "meaning-a", padding: "x".repeat(1000) }] }
          : { ok: false, error: { code: "invalid_search", message: "No search result" } };
        launch = child => queueMicrotask(() => {
          child.stdout.emit("data", Buffer.from(JSON.stringify(response) + "\n"));
          child.emit("close", 0);
        });
        expect((await toolNamed(tools, "kgd_search").execute("failed", { query: "Invented definition" })).isError).toBe(true);
        launch = successfulBridge;
        const result = await toolNamed(tools, "submit_selection").execute("submit", { selections: [{ qid: "first", ranked: ["meaning-a"], abstain: false }] });
        expect(result.isError).toBe(true);
        expect(result.content[0].text).toContain("successful kgd_search");
      }, { questions: questions.slice(0, 1), config: { max_response_bytes: 512 } });
    }
  });

  test("cancelled search, branch and root calls cannot become eligible through late success", async () => {
    for (const operation of ["search", "branch", "root"]) {
      await withRun(async (run, _config, tools) => {
        await registered(tools);
        const ranked = operation === "root" ? [] : ["meaning-a"];
        await toolNamed(tools, "kgd_pack").execute("preview", { references: ranked });
        if (operation === "search") await toolNamed(tools, "kgd_browse").execute("branch", { reference: "source-a", kind: "source" });
        else await toolNamed(tools, "kgd_search").execute("search", { query: "Invented definition" });
        launch = () => {};
        const controller = new AbortController();
        const params = operation === "search" ? { query: "Invented definition" }
          : operation === "branch" ? { reference: "source-a", kind: "source" } : {};
        const pending = toolNamed(tools, operation === "search" ? "kgd_search" : "kgd_browse").execute("cancelled", params, controller.signal);
        controller.abort();
        const child = spawned.at(-1)!.child;
        const result = operation === "search" ? [{ reference: "meaning-a" }]
          : operation === "branch" ? { entries: [{ reference: "meaning-a" }] } : { sources: [], layers: [], terms: [] };
        child.stdout.emit("data", Buffer.from(JSON.stringify({ ok: true, result }) + "\n"));
        child.emit("close", 0);
        expect((await pending).isError).toBe(true);
        expect(child.killed).toBe(true);
        launch = successfulBridge;
        const calls = spawned.length;
        expect((await toolNamed(tools, "submit_selection").execute("submit", { selections: [{ qid: "first", ranked, abstain: operation === "root" }] })).isError).toBe(true);
        expect(spawned).toHaveLength(calls);
        await expect(access(join(run, "submitted-selection.json"))).rejects.toThrow();
      }, { questions: questions.slice(0, 1) });
    }
  });

  test("search and branch observations remain isolated by question while pack previews can be shared", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      await observe(tools, "first", "source-a", "source");
      await toolNamed(tools, "kgd_pack").execute("preview", { references: ["meaning-a"] });
      const selections = questions.map(row => ({ qid: row.qid, ranked: ["meaning-a"], abstain: false }));
      const submit = () => toolNamed(tools, "submit_selection").execute("submit", { selections });
      expect((await submit()).content[0].text).toContain("successful kgd_search");
      await toolNamed(tools, "kgd_search").execute("search-second", { qid: "second", query: "only c" });
      expect((await submit()).content[0].text).toContain("returned by that question's successful kgd_search");
      await toolNamed(tools, "kgd_search").execute("search-second-again", { qid: "second", query: "Invented definition" });
      expect((await submit()).content[0].text).toContain("for that question");
      await toolNamed(tools, "kgd_browse").execute("branch-second", { qid: "second", reference: "source-a", kind: "source" });
      expect((await submit()).isError).toBe(false);
    });
  });

  test("empty-search abstention requires a question's root overview and empty complete pack", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      await toolNamed(tools, "kgd_pack").execute("preview", { references: [] });
      const selections = questions.map(row => ({ qid: row.qid, ranked: [], abstain: true }));
      const submit = () => toolNamed(tools, "submit_selection").execute("submit", { selections });
      expect((await submit()).isError).toBe(true);
      for (const row of questions) await toolNamed(tools, "kgd_search").execute("search", { qid: row.qid, query: "no matches" });
      await toolNamed(tools, "kgd_browse").execute("root-first", { qid: "first" });
      await toolNamed(tools, "kgd_browse").execute("branch-second", { qid: "second", reference: "definitions", kind: "layer" });
      expect((await submit()).content[0].text).toContain("root overview for that question");
      await toolNamed(tools, "kgd_browse").execute("root-second", { qid: "second" });
      expect((await submit()).isError).toBe(false);
    });
  });

  test("submission without any successful preview never calls the writer or creates an artifact", async () => {
    await withRun(async (run, _config, tools) => {
      await registered(tools);
      const result = await toolNamed(tools, "submit_selection").execute("submit", { selections: [
        { qid: "first", ranked: ["meaning-a"], abstain: false },
        { qid: "second", ranked: [], abstain: true },
      ] });
      expect(result.isError).toBe(true);
      expect(result.content[0].text).toContain("earlier successful kgd_pack preview");
      expect(spawn).not.toHaveBeenCalled();
      await expect(access(join(run, "submitted-selection.json"))).rejects.toThrow();
    });
  });

  test("earlier previews support multiple questions without inferring a question from a pack", async () => {
    await withRun(async (run, _config, tools) => {
      await registered(tools);
      const pack = toolNamed(tools, "kgd_pack");
      await observe(tools, "first", "definitions", "layer");
      await observe(tools, "second", "definitions", "layer");
      await pack.execute("preview-a", { references: ["meaning-a", "condition-b"] });
      await pack.execute("preview-b", { references: ["meaning-c"] });
      const selections = [
        { qid: "first", ranked: ["meaning-c"], abstain: false },
        { qid: "second", ranked: ["meaning-a", "condition-b"], abstain: false },
      ];
      const result = await toolNamed(tools, "submit_selection").execute("submit", { selections });
      expect(result.isError).toBe(false);
      expect(JSON.parse(await readFile(join(run, "submitted-selection.json"), "utf8"))).toEqual(selections);
      expect(JSON.parse(spawned.at(-1)!.child.input)).toEqual({ selections, operation: "submit_selection" });
    });
  });

  test("reordered, narrowed or changed final references cannot reuse a different preview", async () => {
    await withRun(async (run, _config, tools) => {
      await registered(tools);
      await toolNamed(tools, "kgd_pack").execute("preview", { references: ["meaning-a", "condition-b"] });
      await toolNamed(tools, "kgd_pack").execute("empty", { references: [] });
      const calls = spawned.length;
      for (const ranked of [["condition-b", "meaning-a"], ["meaning-a"], ["meaning-c"], ["MEANING-A", "condition-b"]]) {
        const result = await toolNamed(tools, "submit_selection").execute("submit", { selections: [
          { qid: "first", ranked, abstain: false }, { qid: "second", ranked: [], abstain: true },
        ] });
        expect(result.isError).toBe(true);
        expect(spawned).toHaveLength(calls);
      }
      await expect(access(join(run, "submitted-selection.json"))).rejects.toThrow();
    });
  });

  test("failed, malformed, omitted or misordered packets do not qualify a selected list", async () => {
    const responses = [
      { ok: false, error: { code: "failed", message: "No whole response" } },
      { ok: true, result: {} },
      { ok: true, result: { entries: [null] } },
      { ok: true, result: { entries: [{ reference: "meaning-a" }], gaps: [{ reference: "condition-b", reason: "byte-budget" }] } },
      { ok: true, result: { entries: [{ reference: "condition-b" }, { reference: "meaning-a" }], gaps: [] } },
    ];
    for (const response of responses) {
      await withRun(async (run, _config, tools) => {
        await registered(tools);
        launch = child => queueMicrotask(() => {
          child.stdout.emit("data", Buffer.from(JSON.stringify(response) + "\n"));
          child.emit("close", 0);
        });
        const preview = await toolNamed(tools, "kgd_pack").execute("preview", { references: ["meaning-a", "condition-b"] });
        expect(JSON.parse(preview.content[0].text)).toEqual(response);
        const calls = spawned.length;
        const result = await toolNamed(tools, "submit_selection").execute("submit", { selections: [
          { qid: "first", ranked: ["meaning-a", "condition-b"], abstain: false },
          { qid: "second", ranked: ["meaning-a", "condition-b"], abstain: false },
        ] });
        expect(result.isError).toBe(true);
        expect(spawned).toHaveLength(calls);
        await expect(access(join(run, "submitted-selection.json"))).rejects.toThrow();
      });
    }
  });

  test("matching complete entries qualify despite scientific gaps and can be reused by multiple questions", async () => {
    await withRun(async (run, _config, tools) => {
      await registered(tools);
      await observe(tools, "first", "source-a", "source");
      await observe(tools, "second", "source-a", "source");
      const response = { ok: true, result: {
        entries: [{ reference: "meaning-a", conditions: ["Qualified condition"] }],
        gaps: [{ reason: "unresolved-source" }, { reference: "dependency", reason: "dependency-not-packed" }],
      } };
      launch = child => queueMicrotask(() => {
        child.stdout.emit("data", Buffer.from(JSON.stringify(response) + "\n"));
        child.emit("close", 0);
      });
      const preview = await toolNamed(tools, "kgd_pack").execute("preview", { references: ["meaning-a"] });
      expect(JSON.parse(preview.content[0].text)).toEqual(response);
      launch = successfulBridge;
      const result = await toolNamed(tools, "submit_selection").execute("submit", { selections: [
        { qid: "first", ranked: ["meaning-a"], abstain: false },
        { qid: "second", ranked: ["meaning-a"], abstain: false },
      ] });
      expect(result.isError).toBe(false);
      expect(JSON.parse(await readFile(join(run, "submitted-selection.json"), "utf8"))).toHaveLength(2);
    });
  });

  test("empty lists also require an empty preview before abstained submission", async () => {
    await withRun(async (run, _config, tools) => {
      await registered(tools);
      const selections = questions.map(row => ({ qid: row.qid, ranked: [], abstain: true }));
      const submit = toolNamed(tools, "submit_selection");
      expect((await submit.execute("no-preview", { selections })).isError).toBe(true);
      expect(spawn).not.toHaveBeenCalled();
      await toolNamed(tools, "kgd_pack").execute("empty-preview", { references: [] });
      for (const question of questions) await observe(tools, question.qid);
      expect((await submit.execute("submit", { selections })).isError).toBe(false);
      expect(JSON.parse(await readFile(join(run, "submitted-selection.json"), "utf8"))).toEqual(selections);
    });
  });

  test("eligible previews are isolated to the current adapter run", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      await toolNamed(tools, "kgd_pack").execute("preview", { references: ["meaning-a"] });
    });
    await withRun(async (run, _config, tools) => {
      await registered(tools);
      const result = await toolNamed(tools, "submit_selection").execute("submit", { selections: [
        { qid: "first", ranked: ["meaning-a"], abstain: false },
        { qid: "second", ranked: ["meaning-a"], abstain: false },
      ] });
      expect(result.isError).toBe(true);
      expect(spawn).not.toHaveBeenCalled();
      await expect(access(join(run, "submitted-selection.json"))).rejects.toThrow();
    });
  });

  test("eligible search and tree observations are isolated to the current adapter instance", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      await observe(tools, "first", "source-a", "source");
    }, { questions: questions.slice(0, 1) });
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      await toolNamed(tools, "kgd_pack").execute("new-preview", { references: ["meaning-a"] });
      const result = await toolNamed(tools, "submit_selection").execute("submit", { selections: [{ qid: "first", ranked: ["meaning-a"], abstain: false }] });
      expect(result.isError).toBe(true);
      expect(result.content[0].text).toContain("successful kgd_search");
    }, { questions: questions.slice(0, 1) });
  });

  test("fails before launching when the actual session spill setting is unavailable or invalid", async () => {
    for (const missing of ["scope", "setting"]) {
      await withRun(async (_run, _config, tools) => {
        await registered(tools);
        activeScopeAvailable = missing !== "scope";
        spillSettingAvailable = missing !== "setting";
        const result = await toolNamed(tools, "kgd_get").execute("read", { references: ["meaning-a"] });
        expect(result.isError).toBe(true);
        expect(result.content[0].text).toContain("spill limit is unavailable");
        expect(spawn).not.toHaveBeenCalled();
      });
    }
    for (const value of [0, -1, "50", true, NaN, Infinity, Number.MAX_VALUE]) {
      await withRun(async (_run, _config, tools) => {
        await registered(tools);
        spillKilobytes = value;
        const result = await toolNamed(tools, "kgd_get").execute("read", { references: ["meaning-a"] });
        expect(result.isError).toBe(true);
        expect(result.content[0].text).toContain("finite positive number");
        expect(spawn).not.toHaveBeenCalled();
      });
    }
  });

  test("a large complete Unicode packet at the actual spill byte limit is delivered and qualifies", async () => {
    const response = { ok: true, result: {
      entries: [{ reference: "meaning-a", prefix: "科学条件".repeat(6000),
        middle: { formula: "∀x ∈ X, ‖T(x)‖ ≤ C‖x‖" }, tail: "末尾条件".repeat(6000), final_condition: "λ > 0" }], gaps: [],
    } };
    const text = JSON.stringify(response);
    const bytes = Buffer.byteLength(text, "utf8");
    expect(bytes).toBeGreaterThan(50 * 1024);
    expect(bytes).toBeGreaterThan(text.length);
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      await observe(tools, "first", "source-a", "source");
      spillKilobytes = bytes / 1024;
      launch = child => queueMicrotask(() => {
        child.stdout.emit("data", Buffer.from(text + "\n"));
        child.emit("close", 0);
      });
      const result = await toolNamed(tools, "kgd_pack").execute("preview", { references: ["meaning-a"] });
      expect(result.isError).toBe(false);
      expect(result.content[0].text).toBe(text);
      expect(JSON.parse(result.content[0].text).result.entries[0].middle).toEqual(response.result.entries[0].middle);
      expect(JSON.parse(result.content[0].text).result.entries[0].final_condition).toBe("λ > 0");
      launch = successfulBridge;
      expect((await toolNamed(tools, "submit_selection").execute("submit", { selections: [{ qid: "first", ranked: ["meaning-a"], abstain: false }] })).isError).toBe(false);
    }, { questions: questions.slice(0, 1), config: { max_response_bytes: bytes } });
  });

  test("responses one UTF-8 byte above the live spill limit never qualify search, branch or pack", async () => {
    for (const operation of ["search", "browse", "pack"]) {
      const entry = { reference: "meaning-a", prefix: "科学条件".repeat(6000), middle: "Complete declaration", tail: "末尾条件".repeat(6000) };
      const response = { ok: true, result: operation === "search" ? [entry] : { entries: [entry], gaps: [] } };
      const text = JSON.stringify(response);
      const bytes = Buffer.byteLength(text, "utf8");
      await withRun(async (run, _config, tools) => {
        await registered(tools);
        if (operation !== "search") await toolNamed(tools, "kgd_search").execute("search", { query: "Invented definition" });
        if (operation !== "browse") await toolNamed(tools, "kgd_browse").execute("branch", { reference: "source-a", kind: "source" });
        if (operation !== "pack") await toolNamed(tools, "kgd_pack").execute("preview", { references: ["meaning-a"] });
        launch = child => queueMicrotask(() => {
          // A live cap change during execution must be checked at delivery, too.
          spillKilobytes = (bytes - 1) / 1024;
          child.stdout.emit("data", Buffer.from(text + "\n"));
          child.emit("close", 0);
        });
        const params = operation === "search" ? { query: "Invented definition" }
          : operation === "browse" ? { reference: "source-a", kind: "source" } : { references: ["meaning-a"] };
        const result = await toolNamed(tools, `kgd_${operation}`).execute("overspill", params);
        expect(result.isError).toBe(true);
        expect(result.content[0].text).toContain("run-local --config overlay");
        expect(result.content[0].text).not.toContain('"ok":true');
        launch = successfulBridge;
        const calls = spawned.length;
        expect((await toolNamed(tools, "submit_selection").execute("submit", { selections: [{ qid: "first", ranked: ["meaning-a"], abstain: false }] })).isError).toBe(true);
        expect(spawned).toHaveLength(calls);
        await expect(access(join(run, "submitted-selection.json"))).rejects.toThrow();
      }, { questions: questions.slice(0, 1), config: { max_response_bytes: bytes } });
    }
  });

  test("large get and inventory output also fails whole rather than being delivered for harness spilling", async () => {
    const response = { ok: true, result: { content: "科学条件".repeat(6000) } };
    for (const name of ["kgd_get", "kgd_inventory"]) {
      await withRun(async (_run, _config, tools) => {
        await registered(tools);
        launch = child => queueMicrotask(() => {
          child.stdout.emit("data", Buffer.from(JSON.stringify(response) + "\n"));
          child.emit("close", 0);
        });
        const params = name === "kgd_get" ? { references: ["meaning-a"] } : { term: "Invented meaning" };
        const result = await toolNamed(tools, name).execute("overspill", params);
        expect(result.isError).toBe(true);
        expect(result.content[0].text).toContain("active tool output spill limit");
      }, { config: { max_response_bytes: 100000 } });
    }
  });

  test("returns complete nested Unicode and late scientific fields across byte chunks", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      const response = { ok: true, result: {
        entries: [{ definition: "在给定作用域内的定义", conditions: ["初始条件", "λ > 0"],
          evidence: { formula: "∀x ∈ X, ‖T(x)‖ ≤ C‖x‖", qualifications: { convention: "只在此范数下" } } }],
        padding: "内容".repeat(1500),
        late_scientific_field: { assumptions: ["最后一个条件必须保留"], conclusion: "含义不因召回而合并" },
      } };
      const raw = Buffer.from(JSON.stringify(response) + "\n");
      launch = child => queueMicrotask(() => {
        for (let offset = 0; offset < raw.length; offset += 17) child.stdout.emit("data", raw.subarray(offset, offset + 17));
        child.emit("close", 0);
      });
      const result = await toolNamed(tools, "kgd_pack").execute("call", { references: ["meaning-a"] });
      expect(result).toEqual({ content: [{ type: "text", text: JSON.stringify(response) }], details: { ok: true }, isError: false });
      expect(JSON.parse(result.content[0].text)).toEqual(response);
    });
  });

  test("keeps bridge error envelopes complete and marks them as errors", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      const response = { ok: false, error: { code: "response_too_large", message: "Whole response exceeds the configured limit." } };
      launch = child => queueMicrotask(() => {
        child.stdout.emit("data", Buffer.from(JSON.stringify(response) + "\n"));
        child.emit("close", 0);
      });
      const result = await toolNamed(tools, "kgd_get").execute("call", { references: ["meaning-a"] });
      expect(result.isError).toBe(true);
      expect(result.details).toEqual({ ok: false });
      expect(JSON.parse(result.content[0].text)).toEqual(response);
    });
  });

  test("accepts a complete response at the byte bound plus its newline", async () => {
    const response = { ok: true, result: { qualification: "末尾条件：λ > 0。" } };
    const raw = Buffer.from(JSON.stringify(response));
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      launch = child => queueMicrotask(() => {
        child.stdout.emit("data", raw);
        child.stdout.emit("data", Buffer.from("\n"));
        child.emit("close", 0);
      });
      const result = await toolNamed(tools, "kgd_get").execute("call", { references: ["meaning-a"] });
      expect(result.isError).toBe(false);
      expect(JSON.parse(result.content[0].text)).toEqual(response);
    }, { config: { max_response_bytes: raw.length } });
  });

  test("rejects the whole oversized response and kills the child rather than truncating", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      launch = child => queueMicrotask(() => {
        child.stdout.emit("data", Buffer.from('{"ok":true,"result":'));
        child.stdout.emit("data", Buffer.from(JSON.stringify("科学条件".repeat(1000)) + "}\n"));
        child.emit("close", 0);
      });
      const result = await toolNamed(tools, "kgd_get").execute("call", { references: ["meaning-a"] });
      expect(result).toEqual({ content: [{ type: "text", text: "Whole bridge response exceeds the configured limit" }],
        details: { ok: false }, isError: true });
      expect(spawned[0].child.killed).toBe(true);
      expect(result.content[0].text).not.toContain('"ok":true');
    }, { config: { max_response_bytes: 160 } });
  });

  test("sanitizes interpreter stderr and nonzero process errors", async () => {
    await withRun(async (run, _config, tools) => {
      await registered(tools);
      launch = child => queueMicrotask(() => {
        child.stderr.emit("data", Buffer.from(`Traceback in ${join(run, "private-interpreter.py")}: secret detail`));
        child.stdout.emit("data", Buffer.from("partial response"));
        child.emit("close", 1);
      });
      const result = await toolNamed(tools, "kgd_get").execute("call", { references: ["meaning-a"] });
      expect(result.content[0].text).toBe("Configured bridge process failed");
      expect(result.isError).toBe(true);
      expect(result.content[0].text).not.toContain(run);
      expect(result.content[0].text).not.toContain("Traceback");
    });
  });

  test("sanitizes synchronous and asynchronous launcher failures", async () => {
    await withRun(async (run, _config, tools) => {
      await registered(tools);
      launch = () => { throw new Error(`ENOENT ${join(run, "private-python")}`); };
      const tool = toolNamed(tools, "kgd_get");
      const synchronous = await tool.execute("call", { references: ["meaning-a"] });
      expect(synchronous.content[0].text).toBe("Configured bridge could not start");
      expect(synchronous.isError).toBe(true);
      launch = child => queueMicrotask(() => {
        child.emit("error", new Error(`EACCES ${join(run, "private-python")}`));
        child.emit("close", -1);
      });
      const asynchronous = await tool.execute("call", { references: ["meaning-a"] });
      expect(asynchronous).toEqual(synchronous);
    });
  });

  test("rejects invalid complete JSON without exposing its contents", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      launch = child => queueMicrotask(() => {
        child.stdout.emit("data", Buffer.from('{"ok":true,"secret":"unterminated'));
        child.emit("close", 0);
      });
      const result = await toolNamed(tools, "kgd_get").execute("call", { references: ["meaning-a"] });
      expect(result.content[0].text).toBe("Bridge returned an invalid complete JSON response");
      expect(result.isError).toBe(true);
    });
  });

  test("an already aborted call does not spawn a process", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      const controller = new AbortController();
      controller.abort();
      const result = await toolNamed(tools, "kgd_get").execute("call", { references: ["meaning-a"] }, controller.signal);
      expect(result.content[0].text).toBe("Compiled knowledge call cancelled");
      expect(result.isError).toBe(true);
      expect(spawn).not.toHaveBeenCalled();
    });
  });

  test("an in-flight abort kills the child and cannot become a late success", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      launch = () => {};
      const controller = new AbortController();
      const pending = toolNamed(tools, "kgd_pack").execute("call", { references: ["meaning-a"] }, controller.signal);
      expect(spawned).toHaveLength(1);
      controller.abort();
      const child = spawned[0].child;
      child.stdout.emit("data", Buffer.from('{"ok":true,"result":{}}\n'));
      child.emit("close", 0);
      const result = await pending;
      expect(child.killed).toBe(true);
      expect(result.content[0].text).toBe("Compiled knowledge call cancelled");
      expect(result.isError).toBe(true);
    });
  });

  test("rejects empty, duplicate and extra-field questions before registration or spawning", async () => {
    const invalid = [
      [], [{ qid: "", question: "A question" }], [{ qid: "a", question: " \n " }],
      [{ qid: "a", question: "First" }, { qid: "a", question: "Second" }],
      [{ qid: "a", question: "Question", extra: "not accepted" }],
      [{ qid: "a" }], [null], { qid: "a", question: "Question" },
    ];
    for (const rows of invalid) {
      await withRun(async (_run, _config, tools) => {
        await expect(registered(tools)).rejects.toThrow("Invalid current question addresses");
        expect(tools).toHaveLength(0);
        expect(spawn).not.toHaveBeenCalled();
      }, { questions: rows });
    }
  });

  test("rejects nonabsolute interpreter/library and invalid or unknown run-local limits", async () => {
    const invalid = [
      { python_interpreter: "relative-python" }, { library_path: "relative-library.json" },
      { byte_budget: 0 }, { reference_limit: true }, { search_limit: 1.5 },
      { max_response_bytes: -1 }, { search_limit: Number.MAX_SAFE_INTEGER + 1 },
      { extra: "not accepted" },
    ];
    for (const config of invalid) {
      await withRun(async (_run, _config, tools) => {
        await expect(registered(tools)).rejects.toThrow("Invalid run-local compiled-tools config");
        expect(tools).toHaveLength(0);
        expect(spawn).not.toHaveBeenCalled();
      }, { config });
    }
  });
});

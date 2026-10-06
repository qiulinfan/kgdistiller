import { afterAll, describe, expect, mock, test } from "bun:test";
import { EventEmitter } from "node:events";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
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
let launch: (child: FakeChild) => void = child => {
  queueMicrotask(() => {
    child.stdout.emit("data", Buffer.from('{"ok":true,"result":{}}\n'));
    child.emit("close", 0);
  });
};
const spawn = mock((command: string, args: string[], options: object) => {
  const child = new FakeChild();
  spawned.push({ command, args, options, child });
  launch(child);
  return child;
});
mock.module("node:child_process", () => ({ spawn }));

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
  launch = child => {
    queueMicrotask(() => {
      child.stdout.emit("data", Buffer.from('{"ok":true,"result":{}}\n'));
      child.emit("close", 0);
    });
  };
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

describe("OMP compiled tools source registration", () => {
  test("erases the SDK type import and registers exactly five tools with declared approvals", async () => {
    expect(javascript).not.toContain("@oh-my-pi/pi-coding-agent");
    await withRun(async (_run, config, tools) => {
      await registered(tools);
      expect(tools.map(tool => [tool.name, tool.approval])).toEqual([
        ["kgd_search", "read"], ["kgd_get", "read"], ["kgd_inventory", "read"],
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
      const result = await toolNamed(tools, "kgd_search").execute("call", { query: "有界映射", limit: 2 });
      expect(result.isError).toBe(false);
      expect(spawned).toHaveLength(1);
      expect(spawned[0].command).toBe(config.python_interpreter);
      expect(spawned[0].args).toEqual([
        "-m", "kgdistiller.omp_compiled_tools", "--config", join(run, "compiled-tools-config.json"), "--output", run,
      ]);
      expect(spawned[0].options).toEqual({ cwd: run, shell: false, stdio: ["pipe", "pipe", "pipe"] });
      expect(JSON.parse(spawned[0].child.input)).toEqual({ query: "有界映射", limit: 2, operation: "search" });
    });
  });

  test("routes each declared operation and keeps pack budget run-local", async () => {
    await withRun(async (_run, _config, tools) => {
      await registered(tools);
      const calls = [
        ["kgd_get", { references: ["meaning-a", "condition-b"] }, "get"],
        ["kgd_inventory", { term: "有界映射" }, "inventory"],
        ["kgd_pack", { references: ["meaning-a"] }, "pack"],
        ["submit_selection", { selections: [
          { qid: "first", ranked: ["meaning-a"], abstain: false },
          { qid: "second", ranked: [], abstain: true },
        ] }, "submit_selection"],
      ] as const;
      for (const [name, params, operation] of calls) {
        await toolNamed(tools, name).execute("call", params);
        expect(JSON.parse(spawned.at(-1)!.child.input)).toEqual({ ...params, operation });
      }
      expect(JSON.parse(spawned[2].child.input)).not.toHaveProperty("byte_budget");
    });
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

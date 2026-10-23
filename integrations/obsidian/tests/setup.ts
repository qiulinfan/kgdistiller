// The plugin calls Obsidian's window timers (window.setTimeout and window.clearTimeout, for popout
// windows); the tests run in Node, where the window is the global object.
const host = globalThis as { window?: unknown };
host.window ??= globalThis;

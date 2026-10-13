import { afterEach, beforeEach, describe, expect, it, vi, type Mock } from "vitest";

import { HOLD_MS, HoldTrigger } from "../src/hold";

const key = (name: string) => ({ key: name });

describe("holding the command key", () => {
  let fire: Mock<() => void>;
  let trigger: HoldTrigger;
  beforeEach(() => {
    vi.useFakeTimers();
    fire = vi.fn<() => void>();
    trigger = new HoldTrigger("Meta", fire);
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("fires after the key has been held alone for HOLD_MS", () => {
    trigger.keydown(key("Meta"));
    vi.advanceTimersByTime(HOLD_MS - 1);
    expect(fire).not.toHaveBeenCalled();
    vi.advanceTimersByTime(1);
    expect(fire).toHaveBeenCalledOnce();
  });

  it("fires once per press, however often the system repeats the keydown", () => {
    trigger.keydown(key("Meta"));
    vi.advanceTimersByTime(HOLD_MS);
    for (let repeat = 0; repeat < 5; repeat += 1) {
      trigger.keydown(key("Meta"));
      vi.advanceTimersByTime(HOLD_MS);
    }
    expect(fire).toHaveBeenCalledOnce();
    trigger.keyup(key("Meta"));
    trigger.keydown(key("Meta"));
    vi.advanceTimersByTime(HOLD_MS);
    expect(fire).toHaveBeenCalledTimes(2);
  });

  it("is cancelled by another key, so shortcuts such as ⌘C never fire it", () => {
    trigger.keydown(key("Meta"));
    trigger.keydown(key("c"));
    trigger.keyup(key("c"));
    vi.advanceTimersByTime(HOLD_MS * 2);
    trigger.keydown(key("Meta"));
    vi.advanceTimersByTime(HOLD_MS * 2);
    expect(fire).not.toHaveBeenCalled();
  });

  it("is cancelled by releasing the key or a mouse press", () => {
    trigger.keydown(key("Meta"));
    trigger.keyup(key("Meta"));
    trigger.keydown(key("Meta"));
    trigger.cancel();
    vi.advanceTimersByTime(HOLD_MS * 2);
    expect(fire).not.toHaveBeenCalled();
  });

  it("forgets a press when the window loses focus, so the next press works", () => {
    trigger.keydown(key("Meta"));
    trigger.reset();
    vi.advanceTimersByTime(HOLD_MS * 2);
    expect(fire).not.toHaveBeenCalled();
    trigger.keydown(key("Meta"));
    vi.advanceTimersByTime(HOLD_MS);
    expect(fire).toHaveBeenCalledOnce();
  });

  it("listens to the platform's own command key only", () => {
    const control = new HoldTrigger("Control", fire);
    control.keydown(key("Meta"));
    vi.advanceTimersByTime(HOLD_MS * 2);
    expect(fire).not.toHaveBeenCalled();
    control.keydown(key("Control"));
    vi.advanceTimersByTime(HOLD_MS);
    expect(fire).toHaveBeenCalledOnce();
  });
});

/**
 * Long-press of the command key (⌘ on macOS, Ctrl elsewhere). The trigger
 * fires once the key has been held alone for HOLD_MS, at most once per press
 * whatever keydown repeats the system sends. Pressing any other key or a mouse
 * button, or releasing the key, cancels it, so shortcuts such as ⌘C and
 * ⌘-click never fire it; leaving the window forgets the press, since its
 * keyup goes to another window.
 */

export const HOLD_MS = 300;

type Key = Pick<KeyboardEvent, "key">;

export class HoldTrigger {
  private timer: ReturnType<typeof setTimeout> | undefined;
  private pressed = false;

  constructor(private readonly key: "Meta" | "Control", private readonly fire: () => void) {}

  keydown(event: Key): void {
    if (event.key !== this.key) {
      this.cancel();
      return;
    }
    if (this.pressed) return;
    this.pressed = true;
    this.timer = setTimeout(() => {
      this.timer = undefined;
      this.fire();
    }, HOLD_MS);
  }

  keyup(event: Key): void {
    if (event.key === this.key) this.pressed = false;
    this.cancel();
  }

  /** Stop a pending hold without ending the press. */
  cancel(): void {
    if (this.timer !== undefined) clearTimeout(this.timer);
    this.timer = undefined;
  }

  /** Forget the press too, as when the window loses focus. */
  reset(): void {
    this.cancel();
    this.pressed = false;
  }
}

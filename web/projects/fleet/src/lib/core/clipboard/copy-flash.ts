import { type Signal, signal } from '@angular/core';

/** How long the "Copied" state stays up after a copy lands. */
const COPIED_FLASH_MS = 1500;

/** A copy action and the transient "Copied" state it drives. */
export interface CopyFlash {
  /** `true` for a moment after a copy lands. */
  readonly copied: Signal<boolean>;
  /** Write `text` to the clipboard; a no-op where the clipboard is unavailable. */
  copy(text: string): void;
}

/** One copy button's clipboard behaviour — the single implementation every copy
 * control in the board and the runner panel shares. */
export function createCopyFlash(): CopyFlash {
  const copied = signal(false);
  return {
    copied: copied.asReadonly(),
    copy(text: string): void {
      const clipboard = globalThis.navigator?.clipboard;
      if (!clipboard) return;
      void clipboard.writeText(text).then(() => {
        copied.set(true);
        setTimeout(() => copied.set(false), COPIED_FLASH_MS);
      });
    },
  };
}

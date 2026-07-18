import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * The action button (issue #78) — the `.act` chrome duplicated (with drift)
 * across `queue-panel.ts` and `runner-panel.ts`: a small bordered button in
 * three variants. Wraps a real native `<button>` so type/disabled/keyboard
 * semantics stay native; the click event passes through by bubbling — a
 * caller binds `(click)` on `<fleet-kit-button>` directly, no `@Output`
 * needed. `:host { display: contents }` keeps the wrapper out of layout, so a
 * caller's flex/grid rules see the button itself.
 */
@Component({
  selector: 'fleet-kit-button',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <button
      type="button"
      class="act"
      [class.primary]="variant() === 'primary'"
      [class.danger]="variant() === 'danger'"
      [disabled]="disabled()"
      [attr.aria-label]="ariaLabel()"
      [attr.data-testid]="testid()"
    >
      <ng-content />
    </button>
  `,
  styles: `
    :host {
      display: contents;
    }
    .act {
      font-family: inherit;
      background: var(--overlay-30);
      border: 1px solid var(--line);
      color: var(--text);
      cursor: pointer;
      padding: 2px 7px;
      font-size: var(--fs-xs);
    }
    .act.primary {
      color: var(--cyan);
    }
    .act.danger {
      color: var(--red);
    }
    .act:hover:not(:disabled) {
      border-color: var(--cyan);
    }
    .act.danger:hover:not(:disabled) {
      border-color: var(--red);
    }
    .act:disabled {
      opacity: 0.4;
      cursor: default;
    }
  `,
})
export class KitButton {
  readonly variant = input<'default' | 'primary' | 'danger'>('default');
  readonly disabled = input(false);
  readonly ariaLabel = input<string | null>(null);
  readonly testid = input<string | null>(null);
}

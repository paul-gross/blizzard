import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * The action button (issue #78) — the `.act` chrome duplicated (with drift)
 * across the ready-queue and runner panels: a small bordered button in
 * three variants. Wraps a real native `<button>` so type/disabled/keyboard
 * semantics stay native; the click event passes through by bubbling — a
 * caller binds `(click)` on `<fleet-kit-button>` directly, no `@Output`
 * needed. `:host { display: contents }` keeps the wrapper out of layout, so a
 * caller's flex/grid rules see the button itself.
 */
@Component({
  selector: 'fleet-kit-button',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-button.html',
  styleUrl: './kit-button.css',
})
export class KitButton {
  readonly variant = input<'default' | 'primary' | 'danger'>('default');
  readonly disabled = input(false);
  readonly ariaLabel = input<string | null>(null);
  readonly testid = input<string | null>(null);
}

import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import { CdkTrapFocus } from '@angular/cdk/a11y';

/**
 * The modal shell (blizzard#392 D6) — the chrome a dialog needs and no dialog under
 * `fleet/lib/kit/` had before this one: a viewport-covering scrim, a centred, framed
 * panel with header/body/footer slots, `role="dialog"`/`aria-modal`, Escape and
 * backdrop dismissal, and focus containment. Presentational only, no client or query
 * dependency (`bzh:frontend-kit-floor`, `bzh:frontend-kit`): it renders exactly what
 * it is handed and reports the two ways a user asks to leave through one `closed`
 * output, never deciding for itself whether closing is allowed.
 *
 * Focus containment is `@angular/cdk/a11y`'s `CdkTrapFocus` (already a workspace
 * dependency via `@angular/cdk/menu`, issue #161) — it cycles Tab within the panel and,
 * with `cdkTrapFocusAutoCapture`, moves focus into the panel the instant it opens; both
 * are directives, not a service, so they cost this component no query/client
 * dependency of their own.
 *
 * Escape is bound on the scrim rather than the panel: a `keydown` fired on any element
 * the focus trap has captured still bubbles up through the panel to the scrim that
 * contains it, so one listener catches it regardless of which control inside currently
 * holds focus. A click on the panel stops propagation before it reaches the scrim's own
 * click handler, so only a genuine backdrop click — never a click that merely bubbled
 * up from inside the panel — dismisses.
 *
 * `open` gates rendering outright (`@if`) rather than a `hidden`/`display:none` toggle,
 * so a closed dialog contributes no element — and no trapped focus — to the page at
 * all, and every open re-mounts a fresh focus-trap capture.
 */
@Component({
  selector: 'fleet-kit-dialog',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkTrapFocus],
  templateUrl: './kit-dialog.html',
  styleUrl: './kit-dialog.css',
})
export class KitDialog {
  /** Whether the dialog is open — the caller's own state; this component asks to
   * close via {@link closed} but never flips it itself. */
  readonly open = input.required<boolean>();

  /** The dialog's accessible name (`aria-label`) — required, since a `role="dialog"`
   * with none is unnamed to assistive tech. */
  readonly ariaLabel = input.required<string>();

  /** The panel's `data-testid`, or `null` for none. */
  readonly testid = input<string | null>(null);

  /** Fires on Escape or a backdrop click — the caller decides what closing means
   * (discard, confirm-then-discard, or refuse) and flips {@link open} itself. */
  readonly closed = output<void>();

  protected onBackdropClick(): void {
    this.closed.emit();
  }

  protected onPanelClick(event: MouseEvent): void {
    event.stopPropagation();
  }

  protected onEscape(): void {
    this.closed.emit();
  }
}

import { ChangeDetectionStrategy, Component, booleanAttribute, input, output } from '@angular/core';

/**
 * The selectable-row chrome (`design/tokens.css`'s `--surface-*` interaction-state
 * scale) — a full-bleed `<button>` carrying the reserved-left-edge, hover/selected
 * background pattern every hand-rolled row list on this scale (the artifacts panel,
 * the timeline selection, the transcripts tab) already drew for itself.
 * This component is now that scale's canonical consumer: it owns no horizontal
 * padding on its own container, the same contract the token doc comment states.
 * Presentational only — the caller supplies the row's content via `<ng-content>`
 * and reads the click through `picked`.
 */
@Component({
  selector: 'fleet-kit-select-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-select-row.html',
  styleUrl: './kit-select-row.css',
})
export class KitSelectRow {
  readonly selected = input(false, { transform: booleanAttribute });
  readonly testid = input<string | null>(null);

  /** The accessible name of the focusable `<button>`, for a row whose projected
   * content alone would not name it. */
  readonly ariaLabel = input<string | null>(null);

  /** The consumer owns the row's layout and chrome: the kit draws no padding, border,
   * hover or selected background, or focus outline in any state, and the button takes
   * its `display`, `flex-direction`, `gap`, `align-items`, `justify-content` and
   * `color` from the host — so a consumer's flex rules on `fleet-kit-select-row`
   * govern the projected content. The consumer draws its own focus indicator on the
   * host (`:focus-within` or `:has(:focus-visible)`). */
  readonly bare = input(false, { transform: booleanAttribute });

  /** Named `picked`, not `select` — `@angular-eslint/no-output-native` forbids an
   * output shadowing the native DOM `select` event. */
  readonly picked = output<void>();
}

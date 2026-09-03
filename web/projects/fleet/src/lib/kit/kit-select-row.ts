import { ChangeDetectionStrategy, Component, booleanAttribute, input, output } from '@angular/core';

/**
 * The selectable-row chrome (`design/tokens.css`'s `--surface-*` interaction-state
 * scale) — a full-bleed `<button>` carrying the reserved-left-edge, hover/selected
 * background pattern every hand-rolled row list on this scale (the artifacts panel,
 * the timeline selection, the transcripts tab, a board card) already drew for itself.
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

  /** Named `picked`, not `select` — `@angular-eslint/no-output-native` forbids an
   * output shadowing the native DOM `select` event. */
  readonly picked = output<void>();
}

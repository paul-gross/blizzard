import { ChangeDetectionStrategy, Component, booleanAttribute, input, output } from '@angular/core';

/**
 * One selectable row inside an inline radio field (blizzard#399 D6) — the
 * label-plus-`<input type="radio">` chrome a native radiogroup needs, with only the
 * label's own content (`<ng-content>`) left to the caller. The radio element itself is
 * templated here, not projected: a projected `<input>` is invisible to
 * `label-has-associated-control`, and this component owning it directly is also the
 * same shape `KitChip` already takes for a `<button>`.
 */
@Component({
  selector: 'fleet-kit-option',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-option.html',
  styleUrl: './kit-option.css',
})
export class KitOption {
  readonly name = input.required<string>();
  readonly checked = input(false);
  readonly disabled = input(false);
  readonly testid = input<string | null>(null);

  /** `flex-start` alignment for multi-line content (e.g. a scope's description
   * beneath its slug) — the default `center` fits a single-line label. */
  readonly alignTop = input(false, { transform: booleanAttribute });

  readonly changed = output<void>();
}

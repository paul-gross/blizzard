import { Option } from '@angular/aria/listbox';
import { ChangeDetectionStrategy, Component, booleanAttribute, inject, input } from '@angular/core';

/**
 * The selectable-row chrome (`design/tokens.css`'s `--surface-*` interaction-state
 * scale) — the reserved-left-edge, hover/selected background pattern every
 * hand-rolled row list on this scale already drew for itself. This component is that
 * scale's canonical consumer: it owns no horizontal padding on its own container, the
 * same contract the token doc comment states.
 *
 * A row is an option of a {@link KitSelectList}: its host carries `role="option"`, the
 * roving tabindex and `aria-selected`; the inner element only draws the chrome. The row must
 * be declared inside its list's element. The caller supplies the row's content via
 * `<ng-content>`; the list reports the open.
 */
@Component({
  selector: 'fleet-kit-select-row',
  changeDetection: ChangeDetectionStrategy.OnPush,
  hostDirectives: [{ directive: Option, inputs: ['value', 'disabled', 'label'] }],
  host: {
    '[attr.data-testid]': 'testid()',
    '[attr.aria-label]': 'ariaLabel()',
    '[class.selected]': 'option.selected()',
    '[class.bare]': 'bare()',
  },
  templateUrl: './kit-select-row.html',
  styleUrl: './kit-select-row.css',
})
export class KitSelectRow {
  protected readonly option = inject<Option<unknown>>(Option);
  readonly testid = input<string | null>(null);

  /** The accessible name of the row, for a row whose projected content alone would not name it. */
  readonly ariaLabel = input<string | null>(null);

  /** The consumer owns the row's layout and chrome: the kit draws no padding, border,
   * hover or selected background, or focus outline in any state, and the inner element takes
   * its `display`, `flex-direction`, `gap`, `align-items`, `justify-content` and
   * `color` from the host — so a consumer's flex rules on `fleet-kit-select-row`
   * govern the projected content. The consumer draws its own focus indicator on the
   * host (`:focus-visible`). */
  readonly bare = input(false, { transform: booleanAttribute });
}

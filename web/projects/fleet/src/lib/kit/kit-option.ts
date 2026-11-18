import { ChangeDetectionStrategy, Component, booleanAttribute, computed, inject, input, output } from '@angular/core';

import { KitOptionGroup } from './kit-option-group';

/**
 * One selectable row inside an inline radio field — the
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
  private readonly group = inject(KitOptionGroup, { optional: true });

  /** The native radio name; optional inside a {@link KitOptionGroup}, which supplies it. */
  readonly name = input<string | null>(null);
  readonly checked = input(false);
  readonly disabled = input(false);

  /** This option's value inside a {@link KitOptionGroup}; the group's value selects the matching option. */
  readonly value = input('');
  readonly testid = input<string | null>(null);

  /** `flex-start` alignment for multi-line content (e.g. a scope's description
   * beneath its slug) — the default `center` fits a single-line label. */
  readonly alignTop = input(false, { transform: booleanAttribute });

  readonly changed = output<void>();

  protected readonly radioName = computed(() => (this.group ? this.group.radioName() : this.name()));
  protected readonly isChecked = computed(() => (this.group ? this.group.value() === this.value() : this.checked()));
  protected readonly isDisabled = computed(() => this.disabled() || !!this.group?.disabled());

  protected onChange(): void {
    this.group?.value.set(this.value());
    this.changed.emit();
  }
}

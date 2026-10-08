import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

/** One selectable option in a {@link KitChips} row. */
export interface KitChipOption {
  readonly value: string;
  readonly label: string;
  readonly disabled?: boolean;
  readonly title?: string;
  /** Optional per-chip test hook, forwarded to the rendered {@link KitChip}. */
  readonly testid?: string;
}

/**
 * One choice chip — a small bordered, selectable pill. Standalone
 * so a caller with a single ad-hoc chip (not a whole options row) can use it
 * directly; {@link KitChips} composes it for the common case of an option
 * list.
 *
 * Fully rounded, matching `kit-badge.ts`'s `soft` variant: the
 * board's soft-pill vocabulary is one shape language, so every chips row
 * reads the same as the badges beside it rather than as a row of hard-edged
 * boxes. Selection is the amber border-and-text highlight.
 */
@Component({
  selector: 'fleet-kit-chip',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-chip.html',
  styleUrl: './kit-chip.css',
})
export class KitChip {
  readonly selected = input(false);
  /** One-shot action chrome omits toggle semantics. */
  readonly action = input(false);
  readonly disabled = input(false);
  readonly title = input<string | null>(null);
  readonly testid = input<string | null>(null);
}

/**
 * A row of choice chips — the inline-option-row shape for a
 * closed set of choices (e.g. a graph's edge choices, a status filter):
 * renders one {@link KitChip} per option, `(choose)` firing the clicked
 * option's `value`.
 *
 * Pills suit a small closed set or a toggle. An open vocabulary that grows with
 * data (every routine, scope, or class named so far) belongs in a
 * {@link KitSelect} instead, which takes the same options and emits the same
 * value.
 */
@Component({
  selector: 'fleet-kit-chips',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitChip],
  templateUrl: './kit-chips.html',
  styleUrl: './kit-chips.css',
})
export class KitChips {
  readonly options = input.required<readonly KitChipOption[]>();
  readonly selectedValue = input<string | null>(null);
  readonly action = input(false);
  readonly disabled = input(false);

  /** Emits the clicked option's `value`. */
  readonly choose = output<string>();

  protected pick(option: KitChipOption): void {
    if (!this.disabled() && !option.disabled) this.choose.emit(option.value);
  }
}

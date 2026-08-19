import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

/** One selectable option in a {@link KitChips} row. */
export interface KitChipOption {
  readonly value: string;
  readonly label: string;
  /** Optional per-chip test hook, forwarded to the rendered {@link KitChip}. */
  readonly testid?: string;
}

/**
 * One choice chip (issue #78) — a small bordered, selectable pill. Standalone
 * so a caller with a single ad-hoc chip (not a whole options row) can use it
 * directly; {@link KitChips} composes it for the common case of an option
 * list.
 *
 * Fully rounded, matching `kit-badge.ts`'s `soft` variant (issue #153): the
 * board's soft-pill vocabulary is one shape language, so every chips row —
 * today the Events tab's filters, the viewport toggle, and the runner chunk
 * detail page's attempt tabs — reads the same as the badges beside it rather
 * than as a row of hard-edged boxes. Selection stays the amber border-and-text
 * highlight; only the shape changed.
 */
@Component({
  selector: 'fleet-kit-chip',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-chip.html',
  styleUrl: './kit-chip.css',
})
export class KitChip {
  readonly selected = input(false);
  readonly testid = input<string | null>(null);
}

/**
 * A row of choice chips (issue #78) — the inline-option-row shape for a
 * closed set of choices (e.g. a graph's edge choices, a status filter):
 * renders one {@link KitChip} per option, `(choose)` firing the clicked
 * option's `value`.
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

  /** Emits the clicked option's `value`. */
  readonly choose = output<string>();
}

import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

/** One selectable option in a {@link KitChips} row. */
export interface KitChipOption {
  readonly value: string;
  readonly label: string;
}

/**
 * One choice chip (issue #78) — a small bordered, selectable pill. Standalone
 * so a caller with a single ad-hoc chip (not a whole options row) can use it
 * directly; {@link KitChips} composes it for the common case of an option
 * list.
 */
@Component({
  selector: 'fleet-kit-chip',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <button type="button" class="chip" [class.selected]="selected()" [attr.data-testid]="testid()">
      <ng-content />
    </button>
  `,
  styles: `
    :host {
      display: contents;
    }
    .chip {
      font-family: inherit;
      background: transparent;
      border: 1px solid var(--line);
      color: var(--text);
      cursor: pointer;
      padding: 1px 6px;
      font-size: var(--fs-xs);
      letter-spacing: 0.04em;
    }
    .chip:hover {
      border-color: var(--cyan);
    }
    .chip.selected {
      border-color: var(--amber-hi);
      color: var(--amber-hi);
    }
  `,
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
  template: `
    <div class="row">
      @for (option of options(); track option.value) {
        <fleet-kit-chip [selected]="option.value === selectedValue()" (click)="choose.emit(option.value)">
          {{ option.label }}
        </fleet-kit-chip>
      }
    </div>
  `,
  styles: `
    :host {
      display: contents;
    }
    .row {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }
  `,
})
export class KitChips {
  readonly options = input.required<readonly KitChipOption[]>();
  readonly selectedValue = input<string | null>(null);

  /** Emits the clicked option's `value`. */
  readonly choose = output<string>();
}

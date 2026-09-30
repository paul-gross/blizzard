import { CdkConnectedOverlay, CdkOverlayOrigin } from '@angular/cdk/overlay';
import { CdkListbox, CdkOption } from '@angular/cdk/listbox';
import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  Injector,
  afterNextRender,
  computed,
  inject,
  input,
  output,
  signal,
  viewChild,
} from '@angular/core';

import type { KitChipOption } from './kit-chips';

/**
 * A single-select dropdown — the control shape for an **open vocabulary**, a
 * value list that grows with data (every routine, scope, or class the fleet
 * has ever named), where a row of {@link KitChips} pills would wrap without
 * bound. `KitChips` stays the choice for a small closed set or a toggle.
 *
 * Takes the same {@link KitChipOption} list and emits `(choose)` with the same
 * value as `KitChips`, so a call site swaps only the element name. Like
 * `KitChips`, `(choose)` fires on **every** pick, re-picking the current option
 * included: the CDK listbox emits its own value change only when the selection
 * moved, so the pick is hooked at the option's click and Enter/Space instead.
 *
 * Built on `@angular/cdk/listbox` (option semantics, roving focus, arrow-key
 * navigation, typeahead) and `@angular/cdk/overlay` (the popup). The trigger
 * is a button carrying `aria-haspopup`/`aria-expanded`/`aria-controls`; opening
 * moves focus into the listbox with the current value active, and `Escape`, a
 * backdrop click, or a pick closes it and returns focus to the trigger.
 */
@Component({
  selector: 'fleet-kit-select',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [CdkConnectedOverlay, CdkOverlayOrigin, CdkListbox, CdkOption],
  templateUrl: './kit-select.html',
  styleUrl: './kit-select.css',
})
export class KitSelect {
  private readonly injector = inject(Injector);

  readonly options = input.required<readonly KitChipOption[]>();
  readonly selectedValue = input<string | null>(null);

  /** Names the listbox, and the trigger (with the selection) when no {@link label} is set. */
  readonly ariaLabel = input.required<string>();

  /** A muted prefix inside the trigger, folded into its accessible name. */
  readonly label = input<string | null>(null);

  /** The trigger button's `data-testid`, or `null` for none. */
  readonly testid = input<string | null>(null);

  /** Emits the picked option's `value`, on every pick. */
  readonly choose = output<string>();

  protected readonly open = signal(false);
  protected readonly listboxId = `fleet-kit-select-${nextId++}`;

  private readonly trigger = viewChild<ElementRef<HTMLButtonElement>>('triggerEl');

  /** The matched option's label, else the raw value (a stale URL value matches no option), else empty. */
  protected readonly selectedLabel = computed(() => {
    const value = this.selectedValue();
    return this.options().find((o) => o.value === value)?.label ?? value ?? '';
  });
  /** The trigger's accessible name: its label (or `ariaLabel`), then the current selection. */
  protected readonly triggerName = computed(() => {
    const name = this.label() || this.ariaLabel();
    const selected = this.selectedLabel();
    return selected ? `${name}: ${selected}` : name;
  });
  protected readonly selection = computed(() => [this.selectedValue()].filter((v): v is string => v !== null));

  protected toggle(): void {
    this.open.update((o) => !o);
  }

  /** Focus the option carrying the current value (or the first) once the popup renders. */
  protected focusActive(): void {
    afterNextRender(
      () => {
        const popup = document.getElementById(this.listboxId);
        if (!popup) return;
        const target =
          popup.querySelector<HTMLElement>('[role="option"][aria-selected="true"]') ??
          popup.querySelector<HTMLElement>('[role="option"]');
        target?.focus();
      },
      { injector: this.injector },
    );
  }

  protected pick(value: string): void {
    this.choose.emit(value);
    this.close();
  }

  protected close(): void {
    if (!this.open()) return;
    this.open.set(false);
    this.trigger()?.nativeElement.focus();
  }
}

let nextId = 0;

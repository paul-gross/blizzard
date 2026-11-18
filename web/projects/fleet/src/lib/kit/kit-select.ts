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
  model,
  output,
  signal,
  viewChild,
} from '@angular/core';

import { FORM_FIELD, type FormValueControl } from '@angular/forms/signals';

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
export class KitSelect implements FormValueControl<string> {
  private readonly injector = inject(Injector);

  /** Present when a `[formField]` binds this select; the bound `value` then wins over `selectedValue`. */
  private readonly formField = inject(FORM_FIELD, {
    optional: true,
    self: true,
  });

  /** The picked value, for a `[formField]` binding (or a `[(value)]` caller). A pick always sets it. */
  readonly value = model('');

  /** Emits on focus leaving the trigger and its popup together, so a bound field becomes touched. */
  readonly touch = output<void>();

  readonly options = input.required<readonly KitChipOption[]>();
  readonly selectedValue = input<string | null>(null);
  readonly disabled = input(false);

  /** Names the listbox, and the trigger (with the selection) when no {@link label} is set. */
  readonly ariaLabel = input.required<string>();

  /** A muted prefix inside the trigger, folded into its accessible name. */
  readonly label = input<string | null>(null);

  /** The trigger button's `data-testid`, or `null` for none. */
  readonly testid = input<string | null>(null);

  /** Emits the picked option's `value`, on every pick. */
  readonly choose = output<string>();

  /** Emits when the popup opens, by any route — pointer, keyboard, or {@link openFromGrid}. */
  readonly opened = output<void>();

  /** Emits when the popup closes, by any route — a pick, Escape, dismissal, or {@link closeFromGrid}. */
  readonly closed = output<void>();

  /**
   * The trigger button's `tabindex`, set by a surrounding grid bridge (`fleetKitGridWidget`)
   * that owns which control is the Tab stop. `null` (the default) leaves the native tab order alone.
   */
  readonly controlTabIndex = input<number | null>(null);

  protected readonly open = signal(false);
  protected readonly listboxId = `fleet-kit-select-${nextId++}`;

  private readonly trigger = viewChild<ElementRef<HTMLButtonElement>>('triggerEl');

  /** The value on show: the bound field's when a `[formField]` binds this select, else the caller's `selectedValue`. */
  protected readonly shown = computed<string | null>(() => (this.formField ? this.value() : this.selectedValue()));
  /** The matched option's label, else the raw value (a stale URL value matches no option), else empty. */
  protected readonly selectedLabel = computed(() => {
    const value = this.shown();
    return this.options().find((o) => o.value === value)?.label ?? value ?? '';
  });
  /** The trigger's accessible name: its label (or `ariaLabel`), then the current selection. */
  protected readonly triggerName = computed(() => {
    const name = this.label() || this.ariaLabel();
    const selected = this.selectedLabel();
    return selected ? `${name}: ${selected}` : name;
  });
  /** The listbox selection: the shown value, only while it is among the options (the listbox rejects any other). */
  protected readonly selection = computed(() => {
    const value = this.shown();
    return value !== null && this.options().some((o) => o.value === value) ? [value] : [];
  });

  /** The native trigger `<button>` — the element a grid bridge points Aria's `focusTarget` at. */
  controlElement(): HTMLButtonElement | undefined {
    return this.trigger()?.nativeElement;
  }

  /** Opens the popup for a grid bridge; a no-op while disabled or already open. */
  openFromGrid(): void {
    if (this.disabled()) return;
    this.setOpen(true);
  }

  /**
   * Closes the popup for a grid bridge; a no-op while already closed. Unlike a pick or Escape it leaves
   * focus where it is — the bridge calls it when the widget deactivated because focus went elsewhere.
   */
  closeFromGrid(): void {
    this.setOpen(false);
  }

  /** Focuses the trigger button. */
  focus(options?: FocusOptions): void {
    this.trigger()?.nativeElement.focus(options);
  }

  protected onTriggerFocusOut(event: FocusEvent): void {
    if (this.open()) return;
    this.touchUnlessInside(event);
  }

  protected onPopupFocusOut(event: FocusEvent): void {
    if (!event.relatedTarget) return;
    this.touchUnlessInside(event);
  }

  private touchUnlessInside(event: FocusEvent): void {
    const next = event.relatedTarget as Node | null;
    if (
      next &&
      (this.trigger()?.nativeElement.contains(next) || document.getElementById(this.listboxId)?.contains(next))
    )
      return;
    this.touch.emit();
  }

  protected toggle(): void {
    if (this.disabled()) return;
    this.setOpen(!this.open());
  }

  private setOpen(next: boolean): void {
    if (this.open() === next) return;
    this.open.set(next);
    if (next) this.opened.emit();
    else this.closed.emit();
  }

  /** Focus the option carrying the current value (or the first) once the popup renders. */
  protected focusActive(): void {
    afterNextRender(
      () => {
        const popup = document.getElementById(this.listboxId);
        if (!popup) return;
        const target =
          popup.querySelector<HTMLElement>('[role="option"][aria-selected="true"]:not([aria-disabled="true"])') ??
          popup.querySelector<HTMLElement>('[role="option"]:not([aria-disabled="true"])');
        target?.focus();
      },
      { injector: this.injector },
    );
  }

  protected pick(value: string): void {
    if (this.disabled() || this.options().find((o) => o.value === value)?.disabled) return;
    this.value.set(value);
    this.choose.emit(value);
    this.close();
  }

  protected close(): void {
    if (!this.open()) return;
    this.setOpen(false);
    this.trigger()?.nativeElement.focus();
  }
}

let nextId = 0;

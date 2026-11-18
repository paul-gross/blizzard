import { Listbox } from '@angular/aria/listbox';
import { Directive, effect, inject, input, output } from '@angular/core';

/**
 * Horizontal single-choice group for projected {@link KitChipOptionDirective}s.
 * `hasSelection` distinguishes a selected null option from no selection. Set
 * `selectionMode` to `explicit` while there is no selection, otherwise `follow`.
 * Give the host an accessible name with `aria-label` or `aria-labelledby`.
 */
@Directive({
  selector: '[fleetKitChipGroup]',
  hostDirectives: [{ directive: Listbox, inputs: ['disabled', 'orientation', 'selectionMode'] }],
  host: { '(click)': 'endClick()' },
})
export class KitChipGroup {
  private readonly listbox = inject(Listbox<unknown>);
  private syncing = false;
  // Focus can select before click reaches the listbox; count both as one pick,
  // while still reporting clicks on an already-selected option.
  private pointerEmitted = false;
  private pointerActive = false;
  private suppressClickSelection = false;
  readonly value = input<unknown>(null);
  readonly hasSelection = input(true);
  readonly valueChange = output<unknown>();

  constructor() {
    effect(() => {
      const value = this.value();
      this.syncing = true;
      this.listbox.value.set(this.hasSelection() ? [value] : []);
      this.syncing = false;
    });
    this.listbox.value.subscribe((values) => {
      if (!this.syncing && !this.suppressClickSelection && values.length) {
        if (this.pointerActive) this.pointerEmitted = true;
        this.valueChange.emit(values[0]);
      }
    });
  }

  pointerDown(): void {
    this.pointerEmitted = false;
    this.pointerActive = true;
  }

  pointerClick(value: unknown): void {
    if (!this.pointerActive || !this.pointerEmitted) this.valueChange.emit(value);
    this.pointerEmitted = false;
    this.pointerActive = false;
    this.suppressClickSelection = true;
  }

  protected endClick(): void { this.suppressClickSelection = false; }
}

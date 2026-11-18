import { ChangeDetectionStrategy, Component, ElementRef, input, model, output, viewChild } from '@angular/core';
import type { FormCheckboxControl } from '@angular/forms/signals';

/**
 * A native independent choice with shared checkbox chrome and controlled state. Binds to a `boolean` field as a
 * `FormCheckboxControl` — the forms contract reserves `checked` for checkboxes and forbids `value` on them.
 */
@Component({
  selector: 'fleet-kit-checkbox',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-checkbox.html',
  styleUrl: './kit-checkbox.css',
})
export class KitCheckbox implements FormCheckboxControl {
  readonly checked = model(false);
  readonly disabled = input(false);
  readonly label = input<string | null>(null);
  readonly ariaLabel = input<string | null>(null);
  readonly testid = input<string | null>(null);
  /** Emits on native blur, so a bound field becomes touched. */
  readonly touch = output<void>();

  private readonly native = viewChild<ElementRef<HTMLInputElement>>('native');

  focus(options?: FocusOptions): void {
    this.native()?.nativeElement.focus(options);
  }

  protected changed(event: Event): void {
    this.checked.set((event.target as HTMLInputElement).checked);
  }
}

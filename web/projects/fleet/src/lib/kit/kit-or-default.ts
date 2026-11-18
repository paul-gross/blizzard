import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  inject,
  input,
  model,
  output,
} from '@angular/core';
import type { FormCheckboxControl } from '@angular/forms/signals';

import { KitChip } from './kit-chips';

/**
 * The explicit default choice beside a value control: a toggle chip bound to the draft's boolean, with the control it
 * governs projected before it. The chip does not disable the projected control — the form's `disabled()` rule does,
 * from the same flag — so the typed text survives choosing and unchoosing the default.
 */
@Component({
  selector: 'fleet-kit-or-default',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitChip],
  templateUrl: './kit-or-default.html',
  styleUrl: './kit-or-default.css',
  host: { '(focusout)': 'onFocusOut($event)' },
})
export class KitOrDefault implements FormCheckboxControl {
  private readonly host = inject<ElementRef<HTMLElement>>(ElementRef);

  readonly checked = model(false);
  readonly disabled = input(false);
  readonly defaultLabel = input.required<string>();
  /** Emits when focus leaves the chip, so a bound field becomes touched. */
  readonly touch = output<void>();

  focus(options?: FocusOptions): void {
    this.host.nativeElement
      .querySelector<HTMLButtonElement>('fleet-kit-chip button')
      ?.focus(options);
  }

  protected toggle(): void {
    this.checked.set(!this.checked());
  }

  protected onFocusOut(event: FocusEvent): void {
    const chip = this.host.nativeElement.querySelector('fleet-kit-chip');
    if (chip && event.target instanceof Node && chip.contains(event.target))
      this.touch.emit();
  }
}

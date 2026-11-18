import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  inject,
  input,
  model,
  output,
  viewChild,
} from '@angular/core';
import type { FormValueControl } from '@angular/forms/signals';

import { KIT_FIELD_LABEL } from './kit-field-label';

/**
 * A whole-number text control that keeps the text as typed (`product:no-silent-reset`). It is a native
 * `type="text" inputmode="numeric"` input, never `type="number"`, which reports `''` for text such as `12x` and so
 * loses it; the form's rule judges the string. An optional `unit` follows the input.
 */
@Component({
  selector: 'fleet-kit-number-input',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-number-input.html',
  styleUrl: './kit-number-input.css',
})
export class KitNumberInput implements FormValueControl<string> {
  readonly value = model('');
  readonly disabled = input(false);
  readonly unit = input<string | null>(null);
  readonly placeholder = input<string | null>(null);
  readonly ariaLabel = input<string | null>(null);
  readonly testid = input<string | null>(null);
  /** Emits on native blur, so a bound field becomes touched. */
  readonly touch = output<void>();

  protected readonly fieldLabel = inject(KIT_FIELD_LABEL, { optional: true });
  private readonly native = viewChild<ElementRef<HTMLInputElement>>('native');

  focus(options?: FocusOptions): void {
    this.native()?.nativeElement.focus(options);
  }

  protected onInput(event: Event): void {
    this.value.set((event.target as HTMLInputElement).value);
  }
}

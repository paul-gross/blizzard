import {
  ChangeDetectionStrategy,
  Component,
  computed,
  ElementRef,
  input,
  linkedSignal,
  model,
  output,
  viewChild,
} from '@angular/core';
import type { FormValueControl } from '@angular/forms/signals';

import { isHexColor } from './kit-swatch-color';

/**
 * A color chosen as a `#rrggbb` — a hex text field beside the browser's native color picker, kept in step. The picker
 * commits when it is dismissed, not on every drag step, so a drag is one commit. It emits only a well-formed lowercase
 * `#rrggbb`: a half-typed or malformed field commits nothing and is marked invalid until it is fixed. Presentational and
 * controlled, like the other kit inputs: the caller owns the committed `value` and passes it back in, and the control
 * knows nothing of which colors the backend accepts.
 */
@Component({
  selector: 'fleet-kit-color-input',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-color-input.html',
  styleUrl: './kit-color-input.css',
})
export class KitColorInput implements FormValueControl<string> {
  /** The committed color, `#rrggbb`; a value that is not one seeds an empty field. */
  readonly value = model('');

  /** `aria-label` of the hex text field. */
  readonly ariaLabel = input<string>('Custom color, as #rrggbb');

  readonly testid = input<string | null>(null);

  /** Locks both controls, e.g. while the caller is saving the color. */
  readonly disabled = input(false);

  /** Emits when focus leaves both inputs, so a bound field becomes touched. */
  readonly touch = output<void>();

  private readonly text = viewChild<ElementRef<HTMLInputElement>>('text');

  /** Focuses the hex text field. */
  focus(options?: FocusOptions): void {
    this.text()?.nativeElement.focus(options);
  }

  protected onBlur(event: FocusEvent): void {
    const next = event.relatedTarget as Node | null;
    if (next && (event.currentTarget as HTMLElement).parentElement?.contains(next)) return;
    this.touch.emit();
  }

  /** What the text field shows: the caller's `value` until the user types over it. */
  protected readonly draft = linkedSignal(() => (isHexColor(this.value()) ? this.value().toLowerCase() : ''));

  protected readonly malformed = computed(() => this.draft() !== '' && !isHexColor(this.draft()));

  /** The native picker needs a valid value of its own; it rests on black until a color is chosen. */
  protected readonly pickerValue = computed(() => (isHexColor(this.draft()) ? this.draft().toLowerCase() : '#000000'));

  protected onText(event: Event): void {
    const text = (event.target as HTMLInputElement).value.trim();
    this.draft.set(text);
    this.commit(text);
  }

  protected onPick(event: Event): void {
    const picked = (event.target as HTMLInputElement).value;
    this.draft.set(picked);
    this.commit(picked);
  }

  private commit(text: string): void {
    if (isHexColor(text)) {
      this.value.set(text.toLowerCase());
    }
  }
}

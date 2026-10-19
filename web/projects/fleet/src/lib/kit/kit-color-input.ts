import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
  linkedSignal,
  output,
} from '@angular/core';

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
export class KitColorInput {
  /** The committed color, `#rrggbb`; a value that is not one seeds an empty field. */
  readonly value = input('');

  /** `aria-label` of the hex text field. */
  readonly ariaLabel = input<string>('Custom color, as #rrggbb');

  readonly testid = input<string | null>(null);

  /** A well-formed lowercase `#rrggbb`, on each commit from either control. */
  readonly valueChange = output<string>();

  /** What the text field shows: the caller's `value` until the user types over it. */
  protected readonly draft = linkedSignal(() =>
    isHexColor(this.value()) ? this.value().toLowerCase() : '',
  );

  protected readonly invalid = computed(
    () => this.draft() !== '' && !isHexColor(this.draft()),
  );

  /** The native picker needs a valid value of its own; it rests on black until a color is chosen. */
  protected readonly pickerValue = computed(() =>
    isHexColor(this.draft()) ? this.draft().toLowerCase() : '#000000',
  );

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
      this.valueChange.emit(text.toLowerCase());
    }
  }
}

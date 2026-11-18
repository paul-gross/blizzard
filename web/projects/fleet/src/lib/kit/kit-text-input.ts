import {
  ChangeDetectionStrategy,
  Component,
  ElementRef,
  booleanAttribute,
  input,
  model,
  output,
  viewChild,
} from '@angular/core';
import type { FormValueControl } from '@angular/forms/signals';

/**
 * The text-control chrome — the font, background, border, and
 * focus-visible outline (`bzh:frontend-kit-floor`). Presentational only, no
 * query/client dependency: it owns the chrome and a plain `value`/`valueChange`
 * pair, leaving form-handling — trimming, per-row draft state, submit wiring —
 * to the consumer.
 *
 * The `multiline` input picks which native element renders.
 */
@Component({
  selector: 'fleet-kit-text-input',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-text-input.html',
  styleUrl: './kit-text-input.css',
})
export class KitTextInput implements FormValueControl<string> {
  /** The control's current value. Two-way: `[value]`/`(valueChange)` keep working for a
   * caller that owns the value, and a `[formField]` binds the same model. */
  readonly value = model('');

  /** Locks the native element; a bound field's `disabled` rule sets it. */
  readonly disabled = input(false);

  readonly placeholder = input<string | null>(null);

  /** `aria-label`, for a control with no visible `<label>` of its own (the chunk
   * dock's per-question answer field names itself this way today). */
  readonly ariaLabel = input<string | null>(null);

  readonly testid = input<string | null>(null);

  /** Renders a `<textarea>` instead of a single-line `<input>` — the gardening run
   * dialog's charge note is the one multi-line consumer today. */
  readonly multiline = input(false, { transform: booleanAttribute });

  /** The `<textarea>`'s visible row count; has no effect on the single-line
   * `<input>`. */
  readonly rows = input(3);

  /** `password` for a value that must not echo. The single-line `<input>` takes it as
   * its type; a multiline control masks each character and keeps its line breaks. */
  readonly type = input<'text' | 'password' | 'number'>('text');

  /** Emits on native blur, so a bound field becomes touched. */
  readonly touch = output<void>();

  private readonly native = viewChild<ElementRef<HTMLInputElement | HTMLTextAreaElement>>('native');

  /** Focuses whichever native element renders — the `<textarea>` when `multiline`. */
  focus(options?: FocusOptions): void {
    this.native()?.nativeElement.focus(options);
  }

  protected onInput(event: Event): void {
    this.value.set((event.target as HTMLInputElement | HTMLTextAreaElement).value);
  }
}

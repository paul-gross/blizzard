import { ChangeDetectionStrategy, Component, booleanAttribute, input, output } from '@angular/core';

/**
 * The text-control chrome — the font, background, border, and
 * focus-visible outline (`bzh:frontend-kit-floor`). Presentational only, no
 * query/client dependency: it owns the chrome and a plain `value`/`valueChange`
 * pair, leaving form-handling — trimming, per-row draft state, submit wiring —
 * to the consumer.
 *
 * One component rather than a split `KitTextInput`/`KitTextarea`: single- and
 * multi-line controls share the same chrome rules, and the only real difference —
 * which native element renders,
 * and whether it takes `rows` — is a single `multiline` input away rather than a
 * second component this kit would have to keep visually identical to the first by
 * hand.
 */
@Component({
  selector: 'fleet-kit-text-input',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-text-input.html',
  styleUrl: './kit-text-input.css',
})
export class KitTextInput {
  /** The control's current value — a plain controlled input, not `ngModel`: the
   * caller owns the value (a local signal or a field on some other state) and
   * passes it back in. */
  readonly value = input('');

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

  /** The single-line `<input>`'s type — `password` for a value that must not echo.
   * No effect on a multiline control. */
  readonly type = input<'text' | 'password'>('text');

  readonly valueChange = output<string>();

  protected onInput(event: Event): void {
    this.valueChange.emit((event.target as HTMLInputElement | HTMLTextAreaElement).value);
  }
}

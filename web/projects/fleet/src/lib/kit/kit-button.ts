import { ChangeDetectionStrategy, Component, ElementRef, input, viewChild } from '@angular/core';

/**
 * The action button — a small bordered button in three variants. Wraps a real native `<button>` so type/disabled/keyboard
 * semantics stay native; the click event passes through by bubbling — a
 * caller binds `(click)` on `<fleet-kit-button>` directly, no `@Output`
 * needed. `:host { display: contents }` keeps the wrapper out of layout, so a
 * caller's flex/grid rules see the button itself.
 */
@Component({
  selector: 'fleet-kit-button',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-button.html',
  styleUrl: './kit-button.css',
})
export class KitButton {
  readonly variant = input<'default' | 'primary' | 'danger'>('default');
  /** `default` is the compact action chrome; `cta` is the larger, more emphatic
   * call-to-action treatment; `icon` is a fixed 30px square for a lone glyph. */
  readonly size = input<'default' | 'cta' | 'icon'>('default');
  /** The native `type`; `submit` makes it a form's submit button. */
  readonly type = input<'button' | 'submit'>('button');
  readonly disabled = input(false);
  readonly ariaLabel = input<string | null>(null);
  readonly title = input<string | null>(null);
  readonly testid = input<string | null>(null);
  /**
   * The native button's `tabindex`, set by a surrounding grid bridge (`fleetKitGridWidget`)
   * that owns which control is the Tab stop. `null` (the default) leaves the native tab order
   * alone, so a button outside a grid behaves as it always has.
   */
  readonly controlTabIndex = input<number | null>(null);

  private readonly button = viewChild<ElementRef<HTMLButtonElement>>('buttonEl');

  /** The native `<button>` — the element a grid bridge points Aria's `focusTarget` at. */
  controlElement(): HTMLButtonElement | undefined {
    return this.button()?.nativeElement;
  }
}

import { ChangeDetectionStrategy, Component, booleanAttribute, input, output } from '@angular/core';

/**
 * The open button under a card — a transparent full-bleed `<button>` that opens the card's subject,
 * stretched beneath the card's own links and controls. Not a list option: a card holds controls of its
 * own, so it takes no part in a {@link KitSelectList}'s keyboard model.
 *
 * The consumer owns the card's layout and chrome: the button takes its `display`, `flex-direction`,
 * `gap`, `align-items`, `justify-content` and `color` from the host, and draws no focus outline; the
 * consumer draws its own on the host (`:focus-within` or `:has(:focus-visible)`).
 */
@Component({
  selector: 'fleet-kit-card-open',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './kit-card-open.html',
  styleUrl: './kit-card-open.css',
})
export class KitCardOpen {
  readonly testid = input<string | null>(null);
  /** The accessible name of the button, for a card whose projected content alone would not name it. */
  readonly ariaLabel = input<string | null>(null);
  /** Whether this card is the one currently open (`aria-current`). */
  readonly current = input(false, { transform: booleanAttribute });
  readonly opened = output<void>();
}

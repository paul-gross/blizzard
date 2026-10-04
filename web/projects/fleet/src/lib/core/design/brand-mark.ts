import { ChangeDetectionStrategy, Component, input } from '@angular/core';

/**
 * The blizzard hub-flake mark — a snowflake drawn as a hub-and-spoke
 * orchestration graph: amber hub, snow spokes, cyan agent-node tips. The
 * canonical drawing and its rationale live in `docs/identity/identity.md`;
 * this is the same small-size geometry the favicon uses, minus the plate.
 *
 * Colors resolve through the design tokens so the mark tracks the theme
 * like every other fleet view.
 */
@Component({
  selector: 'fleet-brand-mark',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './brand-mark.html',
  styleUrl: './brand-mark.css',
})
export class BrandMark {
  /** Rendered size in px — the mark is square. */
  readonly size = input(28);
}

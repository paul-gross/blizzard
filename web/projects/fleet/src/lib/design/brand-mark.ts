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
  template: `
    <svg viewBox="0 0 32 32" [attr.width]="size()" [attr.height]="size()" aria-hidden="true">
      <defs>
        <g id="bz-spoke">
          <line x1="16" y1="11.5" x2="16" y2="6" class="flake" stroke-width="1.4" />
          <line x1="16" y1="8.5" x2="13.45" y2="7.05" class="flake" stroke-width="1.1" />
          <line x1="16" y1="8.5" x2="18.55" y2="7.05" class="flake" stroke-width="1.1" />
          <circle cx="16" cy="4" r="1.8" class="tip" />
        </g>
      </defs>
      <use href="#bz-spoke" />
      <use href="#bz-spoke" transform="rotate(60 16 16)" />
      <use href="#bz-spoke" transform="rotate(120 16 16)" />
      <use href="#bz-spoke" transform="rotate(180 16 16)" />
      <use href="#bz-spoke" transform="rotate(240 16 16)" />
      <use href="#bz-spoke" transform="rotate(300 16 16)" />
      <circle cx="16" cy="16" r="2.8" class="hub" />
      <circle cx="16" cy="16" r="4.4" class="halo" stroke-width="0.8" />
    </svg>
  `,
  styles: `
    :host {
      display: inline-flex;
    }
    .flake {
      stroke: var(--snow);
      stroke-linecap: round;
    }
    .tip {
      fill: var(--cyan);
    }
    .hub {
      fill: var(--amber);
    }
    .halo {
      fill: none;
      stroke: var(--amber);
      opacity: 0.45;
    }
  `,
})
export class BrandMark {
  /** Rendered size in px — the mark is square. */
  readonly size = input(28);
}

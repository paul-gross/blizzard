import { ChangeDetectionStrategy, Component } from '@angular/core';

/**
 * The standard profile-avatar glyph (issue #132) — a plain circle carrying a
 * generic person/"guest user" silhouette, no real identity or image (out of
 * scope for #132). {@link KitMenu}'s `[trigger]` projection slot is this
 * icon's one intended use — both the hub's `AppNavMenu` and the runner's
 * `LocalPanelLayout` project it in place of the menu's default `⋮` glyph, so
 * the shell's profile menu renders identically in both apps rather than each
 * inlining its own SVG.
 *
 * Presentational only, no inputs — a decorative icon with nothing to vary yet.
 */
@Component({
  selector: 'fleet-kit-avatar',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <svg viewBox="0 0 24 24" width="20" height="20" aria-hidden="true">
      <circle class="ring" cx="12" cy="12" r="11" />
      <circle class="head" cx="12" cy="9.5" r="3.6" />
      <path class="shoulders" d="M4.8 19.4c1.3-3.4 4-5.1 7.2-5.1s5.9 1.7 7.2 5.1" />
    </svg>
  `,
  styles: `
    :host {
      display: inline-flex;
    }
    .ring {
      fill: var(--overlay-30);
      stroke: var(--line);
      stroke-width: 1;
    }
    .head {
      fill: var(--label);
    }
    .shoulders {
      fill: none;
      stroke: var(--label);
      stroke-width: 1.6;
      stroke-linecap: round;
    }
  `,
})
export class KitAvatar {}

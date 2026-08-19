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
  templateUrl: './kit-avatar.html',
  styleUrl: './kit-avatar.css',
})
export class KitAvatar {}

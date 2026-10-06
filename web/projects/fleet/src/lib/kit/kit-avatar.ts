import { ChangeDetectionStrategy, Component } from '@angular/core';

/**
 * The standard profile-avatar glyph — a plain circle carrying a
 * generic person/"guest user" silhouette, no real identity or image.
 * Intended for {@link KitMenu}'s `[trigger]` projection slot, in place of the
 * menu's default `⋮` glyph, so every profile menu renders the same icon.
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

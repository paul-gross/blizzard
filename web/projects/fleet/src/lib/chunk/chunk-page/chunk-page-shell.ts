import { ChangeDetectionStrategy, Component } from '@angular/core';

/**
 * The shared chunk-detail-page shell — the chrome `ChunkPage` lays its regions into.
 * Five projection slots fixed in this DOM order: `[chunk-page-back]` (the
 * back-to-board link), `[chunk-page-notice]` (an operator-action error/outcome line;
 * the slot renders no marker when nothing is projected), `[chunk-page-header]`
 * (the chunk identity — {@link ChunkPageHeader}), `[chunk-page-tabs]` (the tab strip),
 * and the default slot (the active tab's own body). Every slot is projected as a
 * **direct** child of `.cps` (no wrapper `<div>`), so flex-item blockification gives
 * each its full width; a slot's own spacing stays on the projected content.
 *
 * Owns the outer flex-column chrome (height-capped, `overflow: hidden`, no padding)
 * and the active-tab body's chrome (`.cps-body`: `flex: 1; min-height: 0;
 * position: relative`) — the positioned ancestor {@link KitAsyncState}'s
 * absolutely-centered status line resolves against.
 *
 * The tab strip slot carries no shell-owned `flex` rule: `KitTabs`' own
 * `:host { display: contents }` (`kit-tabs.ts`) generates no box for one to apply to.
 */
@Component({
  selector: 'fleet-chunk-page-shell',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './chunk-page-shell.html',
  styleUrl: './chunk-page-shell.css',
})
export class ChunkPageShell {}

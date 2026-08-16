import { ChangeDetectionStrategy, Component } from '@angular/core';

/**
 * The shared chunk-detail-page shell — the chrome the hub's `ChunkPage` and
 * the runner's `ChunkDetailPage` each hand-rolled a slightly different copy
 * of, which is exactly how they drifted (the hub's back bar ran flush to the
 * screen edge with a bare tab strip below it; the runner's carried its own
 * `padding`/`gap` around the same regions, reading as a border of dead space
 * the hub never had). One shell, five projection slots fixed in this DOM
 * order: `[chunk-page-back]` (the back-to-board link), `[chunk-page-notice]`
 * (an operator-action error/outcome line — a page with none of those
 * projects nothing, and the slot renders no marker), `[chunk-page-header]`
 * (the chunk identity — {@link ChunkPageHeader}), `[chunk-page-tabs]` (the
 * tab strip), and the default slot (the active tab's own body). Sibling to
 * {@link AppShell} (`fleet/lib/app-shell/`) in shape and in the same
 * no-CSS-reaches-across-the-projection-boundary stance its own doc comment
 * states: every slot here is projected as a **direct** child of `.cps` (no
 * wrapper `<div>`), so ordinary flex-item blockification is what gives each
 * slot its full width — the same mechanism {@link AppShell} relies on rather
 * than trying to style content it cannot see past the boundary.
 *
 * Owns the outer flex-column chrome (height-capped, `overflow: hidden`, no
 * padding of its own — a page's back bar/tab strip run flush to this
 * component's own edges) and the active-tab body's chrome (`.cps-body`:
 * `flex: 1; min-height: 0; position: relative`) — the positioned ancestor
 * {@link KitAsyncState}'s absolutely-centered status line resolves against,
 * whether that status is the runner's page-level "FAILED TO LOAD CHUNK" or
 * the hub's own loading/error line, both of which used to carry their own,
 * separately-declared positioned wrapper (`.body` / `.rest`) for exactly
 * this. A slot that owns its own spacing — the back link's `text-decoration`
 * reset, an operator notice's border/color, {@link ChunkPageHeader}'s own
 * `margin`/`padding` — keeps that spacing on itself for the same
 * projection-boundary reason `.back-row { text-decoration: none }` still
 * lives on each page's own back-link markup: this shell cannot style a node
 * it does not render.
 *
 * The tab strip slot carries no shell-owned `flex` rule: `KitTabs`' own
 * `:host { display: contents }` (`kit-tabs.ts`) means the tag itself
 * generates no box, so a `flex` declaration aimed at it (both pages'
 * `fleet-kit-tabs { flex: none }`, dead before this extraction) never had
 * anything to apply to — `KitTabStrip`'s own `:host` (`kit-tab.ts`) already
 * fixes the strip's 32px height outright, so nothing here needs to constrain
 * it further.
 */
@Component({
  selector: 'fleet-chunk-page-shell',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="cps">
      <ng-content select="[chunk-page-back]" />
      <ng-content select="[chunk-page-notice]" />
      <ng-content select="[chunk-page-header]" />
      <ng-content select="[chunk-page-tabs]" />
      <div class="cps-body"><ng-content /></div>
    </div>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
      min-height: 0;
    }
    .cps {
      display: flex;
      flex-direction: column;
      height: 100%;
      min-height: 0;
      overflow: hidden;
    }
    /* Positioned and height-bearing so a projected KitAsyncState's absolutely
       centered status line has a box to center in, whichever page's own
       "nothing to show yet" state it renders. */
    .cps-body {
      display: flex;
      flex-direction: column;
      flex: 1;
      min-height: 0;
      position: relative;
    }
  `,
})
export class ChunkPageShell {}

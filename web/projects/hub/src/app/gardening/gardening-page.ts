import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { injectHubGardenProposalsQuery, isGardenProposalWaiting, KitCountBadge, KitTab, KitTabStrip } from 'fleet';

/**
 * The `/gardening` route (blizzard#397) — a top-level tab beside the board, not a
 * panel inside it (`blizzard-product:/plans/garden/user-interface.md` §Where it
 * lives). The app shell above this page is untouched; this page owns only its own
 * second strip, divided along the garden machinery's own nouns, in this fixed order
 * — scopes, routines, runs, findings, proposals — each a real child route
 * (`app.routes.ts`) so a sub-tab is a deep link, not this component's own selection
 * state. All five are unrelated concepts (a scope is where a routine sweeps, a
 * routine is a declared strategy, a run is a sweep, a finding is a durable record a
 * sweep may add to or resolve, a proposal is a suggestion waiting on a person) that
 * only used to share two combined surfaces; each gets its own tab and its own list
 * now. Landing the default redirect on Scopes (the first tab) rather than Routines
 * follows straight from the requested tab order — Scopes is the leftmost tab now,
 * so it is what the bare `/gardening` path lands on.
 *
 * Only Proposals carries a waiting count, since only proposals are waiting on
 * somebody — Scopes, routines, runs, and findings accumulate, they do not wait. The
 * count comes off the same `GET /api/garden-proposals` read the (later) docket
 * sheet itself will read, so the two never disagree.
 */
@Component({
  selector: 'app-gardening-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitCountBadge, KitTab, KitTabStrip, RouterLink, RouterLinkActive, RouterOutlet],
  templateUrl: './gardening-page.html',
  styleUrl: './gardening-page.css',
})
export class GardeningPage {
  private readonly proposalsQuery = injectHubGardenProposalsQuery();

  /** How many proposals are waiting on a person right now — the strip's own
   * "urgent count" (only Proposals carries one, per the plan). */
  protected readonly proposalsWaitingCount = computed(
    () => (this.proposalsQuery.data() ?? []).filter(isGardenProposalWaiting).length,
  );
}

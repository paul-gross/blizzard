import { ChangeDetectionStrategy, Component, computed } from '@angular/core';
import { RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { injectHubGardenProposalsQuery, isGardenProposalWaiting, KitTab, KitTabStrip } from 'fleet';

/**
 * The `/gardening` route (blizzard#397) — a top-level tab beside the board, not a
 * panel inside it (`blizzard-product:/plans/garden/user-interface.md` §Where it
 * lives). The app shell above this page is untouched; this page owns only its own
 * second strip, divided along the garden machinery's own nouns — routines, runs and
 * findings, proposals — each a real child route (`app.routes.ts`) so a sub-tab is a
 * deep link, not this component's own selection state. Scopes get no sub-tab: they
 * are a vocabulary the other three are read through, managed where they are used.
 *
 * Only Proposals carries a waiting count, since only proposals are waiting on
 * somebody — Routines and runs accumulate, they do not wait. The count comes off
 * the same `GET /api/garden-proposals` read the (later) docket sheet itself will
 * read, so the two never disagree.
 *
 * This chunk builds the shell only: the three children each render an empty state
 * (`plans/garden/user-interface.md`'s "Out of Scope" — every sheet's real content is
 * its own later issue).
 */
@Component({
  selector: 'app-gardening-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitTab, KitTabStrip, RouterLink, RouterLinkActive, RouterOutlet],
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

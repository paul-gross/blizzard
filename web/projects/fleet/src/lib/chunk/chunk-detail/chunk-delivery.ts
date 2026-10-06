import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import type { ChunkDetail, LandedRepoView, PrView } from '../../api/hub';

/** How many leading characters of a landed commit's sha the delivery row shows. */
const SHA_LENGTH = 7;

/** One repo's delivery: its PR (open or closed) and its landed commit, either of which may be absent. */
interface DeliveryRow {
  readonly repo: string;
  readonly openPr: PrView | null;
  readonly closedPr: PrView | null;
  readonly landed: LandedRepoView | null;
  readonly sha: string | null;
}

@Component({
  selector: 'fleet-chunk-delivery',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './chunk-delivery.html',
  styleUrl: './chunk-delivery.css',
})
export class ChunkDelivery {
  readonly detail = input.required<ChunkDetail>();

  /** One row per repo, in order of first appearance across open PRs, closed PRs, then landed repos. */
  protected readonly rows = computed<readonly DeliveryRow[]>(() => {
    const d = this.detail();
    const byRepo = new Map<string, { openPr: PrView | null; closedPr: PrView | null; landed: LandedRepoView | null }>();
    const entry = (repo: string) => {
      let e = byRepo.get(repo);
      if (e === undefined) {
        e = { openPr: null, closedPr: null, landed: null };
        byRepo.set(repo, e);
      }
      return e;
    };
    for (const pr of d.open_prs ?? []) entry(pr.repo).openPr = pr;
    for (const pr of d.closed_prs ?? []) entry(pr.repo).closedPr = pr;
    for (const landed of d.landed_repos ?? []) entry(landed.repo).landed = landed;
    return [...byRepo].map(([repo, e]) => ({
      repo,
      ...e,
      sha: e.landed === null ? null : e.landed.commit_hash.slice(0, SHA_LENGTH),
    }));
  });
}

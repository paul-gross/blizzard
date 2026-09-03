import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute } from '@angular/router';
import {
  asyncState,
  FleetFindingPanel,
  hasPermission,
  injectHubFindingQuery,
  injectMeQuery,
  type FindingPanelTriageVerb,
  type FindingPanelVm,
  type FindingTriageVerb,
  type FindingView,
  type KitAsyncStateValue,
} from 'fleet';
import { map } from 'rxjs';

import { GardeningFindingTriageDialog } from './gardening-finding-triage-dialog';
import { injectFindingWorkItemLookup } from './gardening-finding-work-item-lookup';

/**
 * The selected finding's own detail — the right-hand child of
 * `/gardening/findings` (`gardening-findings-page.ts` owns the filtered list
 * beside it), and where triage itself lives. Mounted by both of that route's
 * children, so the bare one renders the panel's own "nothing selected" empty
 * state.
 *
 * A container: it reads the finding by id and forwards a plain view model to the
 * presentational {@link FleetFindingPanel}, which injects no query of its own. The
 * read is `injectHubFindingQuery`, the single-finding read, not the docket's by-id
 * fan-out: the fan-out drops a failed read so one stale id can't blank an evidence
 * table of many, and here the one id *is* the pane, so a 404 has to arrive as an
 * error rather than as an empty result wearing the "nothing selected" copy. The read
 * is independent of the bucket the list is filtering: keeping the URL's finding and
 * the list's filters in agreement is the list's job, not this pane's.
 */
@Component({
  selector: 'app-gardening-finding-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetFindingPanel, GardeningFindingTriageDialog],
  templateUrl: './gardening-finding-detail.html',
  styleUrl: './gardening-detail-host.css',
})
export class GardeningFindingDetail {
  private readonly route = inject(ActivatedRoute);
  private readonly meQuery = injectMeQuery();

  /** The `findingId` route param, or `null` on the bare child route. */
  protected readonly findingId = toSignal(this.route.paramMap.pipe(map((params) => params.get('findingId'))), {
    initialValue: null,
  });

  private readonly findingQuery = injectHubFindingQuery(this.findingId);

  private readonly selectedFinding = computed<FindingView | null>(() => {
    const id = this.findingId();
    if (id === null) return null;
    const finding = this.findingQuery.data() ?? null;
    return finding?.finding_id === id ? finding : null;
  });

  /** Resolves an accepted-and-minted proposal's work item onto a finding id. */
  private readonly workItemLookup = injectFindingWorkItemLookup();

  /** `introducedRev` carries `FindingView.introduced` verbatim — a git revision, not
   * a timestamp (`finding-panel.ts`'s own doc comment on why it never rides
   * `fleet-when`). `introducedAt` and `firstObservedAt` carry `FindingView`'s two
   * instants verbatim; `introducedAt` is null wherever the hub never resolved the
   * commit (`finding-panel.ts`'s own doc comment on what that means). */
  protected readonly findingPanelVm = computed<FindingPanelVm | null>(() => {
    const finding = this.selectedFinding();
    if (finding === null) return null;
    return {
      findingId: finding.finding_id,
      findingClass: finding.class,
      locus: finding.locus,
      state: finding.state,
      observedCount: finding.observed_count,
      introducedRev: finding.introduced ?? null,
      introducedAt: finding.introduced_at ?? null,
      firstObservedAt: finding.first_observed_at ?? null,
      lastSeenAt: finding.last_seen_at,
      summary: finding.summary,
      note: finding.note ?? null,
      workItem: this.workItemLookup.workItemFor(finding.finding_id),
    };
  });

  /** "Nothing selected" is its own rest state, branched before consulting the
   * read's own async state (`bzh:frontend-empty-state-gated`). */
  protected readonly findingPanelState = computed<KitAsyncStateValue>(() =>
    this.findingId() === null ? 'empty' : asyncState(this.findingQuery, this.findingPanelVm() === null),
  );

  /** Whether the current identity may triage findings (`chunk:control`) — `null`/
   * pending resolves to `false`. */
  protected readonly canControl = computed(() => hasPermission(this.meQuery.data(), 'chunk:control'));

  /** The single-finding action the triage dialog is open against — `null` closes
   * it. Only the finding panel's own `triage` output ({@link onPanelTriage}) sets
   * it, `findingIds` always carrying that one selected finding's id — the dialog
   * itself takes a list (the API routes are plural, the runner and CLI also drive
   * them), this container just never has more than one id to hand it. */
  protected readonly triagingAction = signal<{ verb: FindingTriageVerb; findingIds: readonly string[] } | null>(
    null,
  );

  protected onPanelTriage(verb: FindingPanelTriageVerb): void {
    const id = this.findingId();
    if (id === null) return;
    this.triagingAction.set({ verb, findingIds: [id] });
  }
}

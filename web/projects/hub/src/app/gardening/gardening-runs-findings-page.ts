import { ChangeDetectionStrategy, Component, computed, inject, signal } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import {
  asyncState,
  defaultRoutineWindow,
  FleetFindingList,
  FleetRunDelta,
  FleetRunList,
  hasPermission,
  injectHubFindingsBucketQuery,
  injectHubGardenProposalsQuery,
  injectHubRoutinesQuery,
  injectHubRunDeltaQuery,
  injectHubRunsQuery,
  injectHubScopesQuery,
  injectHubWorkItemsQuery,
  injectMeQuery,
  isFindingOutflow,
  isFindingWithdrawn,
  KitChips,
  type FindingListRowVm,
  type FindingTriageVerb,
  type FindingView,
  type KitAsyncStateValue,
  type KitChipOption,
  type ProposalWorkItemVm,
  type RoutineView,
  type RunDeltaVm,
  type RunListDeliveredSetVm,
  type RunListRowVm,
  type ScopeView,
  type WorkItemPointer,
  type WorkItemView,
} from 'fleet';
import { map } from 'rxjs';

import { GardeningFindingTriageDialog } from './gardening-finding-triage-dialog';

/** One `revisions` map, rendered the same `repo@revision, …` label
 * `gardening-routines-page.ts`'s own `lastSwept` reduction uses — sorted so the label
 * is deterministic across repeated reads of the same set. */
function revisionsLabel(revisions: Record<string, string>): string {
  return (
    Object.entries(revisions)
      .sort(([a], [b]) => a.localeCompare(b))
      .map(([repo, rev]) => `${repo}@${rev}`)
      .join(', ') || '—'
  );
}

const ALL_CLASSES = 'all';
const ALL_STATES = 'all';

/** Every real class chip's value carries this prefix — `class` is opaque,
 * deployment-chosen vocabulary (`gardening-proposals-page.ts`'s own D2 comment),
 * so a real class named `all` can never collide with {@link ALL_CLASSES}. */
const CLASS_VALUE_PREFIX = 'class:';

/** The finding state vocabulary itself — fixed (`FindingView.state`'s own closed
 * set, `finding-state.ts`'s own domain fact), unlike class, so the state chips
 * carry no prefix and no collision guard: none of the seven literally reads `all`. */
const FINDING_STATES: readonly string[] = [
  'live',
  'gone',
  'resolved',
  'gone-confirmed',
  'wont-fix',
  'not-a-finding',
  'superseded',
];

/**
 * The `/gardening/runs-and-findings` sub-tab (blizzard#401 Phase 3, blizzard#402 Phases 1-4,
 * `plans/garden/user-interface.md`'s "Reading what a run saw" and "Triaging what's
 * left" sections) — the run list, the selected run's own delta, and the findings
 * triage bucket for a routine/scope pair. `graphs-page.ts`'s own list-stays-mounted
 * shape: both `runs-and-findings` and `runs-and-findings/:chunkId` render this one
 * component, and the optional `chunkId` route param drives which run's delta shows.
 *
 * A container: it injects every read and derives every view model the
 * presentational {@link FleetRunList}, {@link FleetRunDelta}, and
 * {@link FleetFindingList} need — none of the three injects a query of its own.
 * The findings bucket's routine/scope defaults to the selected run's own
 * (`deltaVm()`'s `routineName`/`scopeSlug`) but an explicit pick overrides it,
 * `gardening-proposals-page.ts`'s own explicit-pick-else-derived `selectedId`
 * shape. Class and state filtering happens client-side (D3): class chips come from
 * the fetched bucket's own `class` values, never a hardcoded vocabulary (D2); state
 * chips are the fixed seven-value vocabulary above. An accepted-and-minted
 * proposal's linked work item (D4) is resolved once for the whole bucket — every
 * proposal's `findings` list maps its own finding ids to that proposal's own
 * `source`/`ref` pointer, and every distinct pointer fans out through
 * `injectHubWorkItemsQuery` (Phase 1) — and attached to the matching row(s); a
 * finding named by no accepted-and-minted proposal gets `workItem: null` and
 * otherwise renders exactly like any other row.
 */
@Component({
  selector: 'app-gardening-runs-findings-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetRunList, FleetRunDelta, FleetFindingList, GardeningFindingTriageDialog, KitChips],
  templateUrl: './gardening-runs-findings-page.html',
  styleUrl: './gardening-runs-findings-page.css',
})
export class GardeningRunsFindingsPage {
  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);
  private readonly meQuery = injectMeQuery();

  /** The list's fixed reporting window — `gardening-routines-page.ts`'s own
   * `window`, computed once at construction rather than re-derived per render; a page
   * reload is what refreshes it. Shares the routine trend/sweeps vocabulary (D5)
   * rather than the read's own 24-hour server default, so an operator sees the same
   * "last 28 days" a routine's own trend already reports. */
  private readonly window = defaultRoutineWindow(Date.now());

  private readonly runsQuery = injectHubRunsQuery(() => this.window.since);

  /** The `chunkId` route param, or `null` on the bare `runs-and-findings` list route —
   * `graphs-page.ts`'s own `graphId` read. */
  protected readonly chunkId = toSignal(this.route.paramMap.pipe(map((params) => params.get('chunkId'))), {
    initialValue: null,
  });

  private readonly deltaQuery = injectHubRunDeltaQuery(() => this.chunkId());

  protected readonly listRows = computed<readonly RunListRowVm[]>(() =>
    (this.runsQuery.data() ?? []).map((row) => ({
      chunkId: row.chunk_id,
      routineName: row.routine_name,
      scopeSlug: row.scope_slug,
      mode: row.mode,
      mintedAt: row.minted_at,
      outcome: row.outcome,
      escalated: row.escalation !== null,
      delivered: row.delivered.map(
        (d): RunListDeliveredSetVm => ({
          findingSetId: d.finding_set_id,
          revisionsLabel: revisionsLabel(d.revisions),
          measurement: d.measurement,
        }),
      ),
    })),
  );

  protected readonly listState = computed<KitAsyncStateValue>(() =>
    asyncState(this.runsQuery, this.listRows().length === 0),
  );

  protected readonly deltaVm = computed<RunDeltaVm | null>(() => {
    const delta = this.deltaQuery.data();
    if (delta === undefined) return null;
    return {
      chunkId: delta.chunk_id,
      routineName: delta.routine_name,
      scopeSlug: delta.scope_slug,
      mode: delta.mode,
      outcome: delta.outcome,
      escalation:
        delta.escalation === null
          ? null
          : {
              nodeName: delta.escalation.node_name,
              takeoverCommand: delta.escalation.takeover_command,
              wrappedTakeoverCommand: delta.escalation.wrapped_takeover_command,
            },
      sets: delta.sets.map((set) => ({
        findingSetId: set.finding_set_id,
        revisionsLabel: revisionsLabel(set.revisions),
        measurement: set.measurement,
        added: set.added.map((a) => ({
          findingId: a.finding_id,
          findingClass: a.class,
          locus: a.locus,
          summary: a.summary,
          introduced: a.introduced,
        })),
        observed: set.observed,
        gone: set.gone.map((g) => ({ findingId: g.finding_id, note: g.note })),
      })),
    };
  });

  /** `chunkId() === null` branches out *before* `asyncState` — `deltaQuery` is
   * `enabled: false` then, which reports `isPending()` forever
   * (`query-state.ts`'s own documented trap), so "nothing selected" must resolve to
   * `'empty'` directly rather than fall into a permanent loading spinner. Once a run
   * is selected, `isEmpty` is always `false` (D4: a run with zero delivered sets
   * still renders as a normal, fully-read row, not an empty state) — only loading and
   * error need distinguishing there. */
  protected readonly deltaState = computed<KitAsyncStateValue>(() =>
    this.chunkId() === null ? 'empty' : asyncState(this.deltaQuery, false),
  );

  protected select(chunkId: string): void {
    void this.router.navigate(['/gardening', 'runs-and-findings', chunkId]);
  }

  // --- Findings triage bucket (blizzard#402 Phase 4) ---

  private readonly routinesQuery = injectHubRoutinesQuery();
  private readonly scopesQuery = injectHubScopesQuery();

  protected readonly routines = computed<readonly RoutineView[]>(() => this.routinesQuery.data() ?? []);
  protected readonly scopes = computed<readonly ScopeView[]>(() => this.scopesQuery.data() ?? []);

  /** The operator's explicit routine/scope pick, `null` until one is made. */
  private readonly explicitRoutine = signal<string | null>(null);
  private readonly explicitScope = signal<string | null>(null);

  /** The bucket read's effective routine/scope: the explicit pick if made, else the
   * selected run's own (`deltaVm()`'s `routineName`/`scopeSlug`) —
   * `gardening-proposals-page.ts`'s own explicit-pick-else-derived `selectedId`
   * shape, applied to a pair instead of a single id. Both stay `null` (the bucket's
   * own "nothing chosen yet" rest state) while no run is selected and no explicit
   * pick has been made. */
  protected readonly selectedRoutine = computed<string | null>(
    () => this.explicitRoutine() ?? this.deltaVm()?.routineName ?? null,
  );
  protected readonly selectedScope = computed<string | null>(
    () => this.explicitScope() ?? this.deltaVm()?.scopeSlug ?? null,
  );

  protected onRoutineChoose(event: Event): void {
    this.explicitRoutine.set((event.target as HTMLSelectElement).value);
  }

  protected onScopeChoose(event: Event): void {
    this.explicitScope.set((event.target as HTMLSelectElement).value);
  }

  private readonly bucketQuery = injectHubFindingsBucketQuery(
    () => this.selectedRoutine(),
    () => this.selectedScope(),
  );

  private readonly bucketRows = computed<readonly FindingView[]>(() => this.bucketQuery.data() ?? []);

  /** `null` means every class — `gardening-proposals-page.ts`'s own reading of an
   * "All classes" chip, so a real class can never collide with it. */
  protected readonly classFilter = signal<string | null>(null);
  protected readonly stateFilter = signal<string | null>(null);

  /** Every class present in the fetched bucket, alphabetized, ahead of an "All
   * classes" chip — never a hardcoded vocabulary (D2). */
  protected readonly classChips = computed<readonly KitChipOption[]>(() => {
    const classes = Array.from(new Set(this.bucketRows().map((f) => f.class))).sort((a, b) => a.localeCompare(b));
    return [
      { value: ALL_CLASSES, label: 'All classes', testid: 'gardening-finding-class-all' },
      ...classes.map((c) => ({
        value: CLASS_VALUE_PREFIX + c,
        label: c,
        testid: `gardening-finding-class-item-${c}`,
      })),
    ];
  });

  protected readonly classChipValue = computed<string>(() => {
    const cls = this.classFilter();
    return cls === null ? ALL_CLASSES : CLASS_VALUE_PREFIX + cls;
  });

  protected onClassChoose(value: string): void {
    this.classFilter.set(value === ALL_CLASSES ? null : value.slice(CLASS_VALUE_PREFIX.length));
  }

  protected readonly stateChips: readonly KitChipOption[] = [
    { value: ALL_STATES, label: 'All states', testid: 'gardening-finding-state-all' },
    ...FINDING_STATES.map((s) => ({ value: s, label: s, testid: `gardening-finding-state-item-${s}` })),
  ];

  protected onStateChoose(value: string): void {
    this.stateFilter.set(value === ALL_STATES ? null : value);
  }

  /** The bucket, filtered client-side by class and state (D3) — the set every row
   * VM and the finding-list's own read derive from. */
  private readonly filteredBucket = computed<readonly FindingView[]>(() => {
    const cls = this.classFilter();
    const st = this.stateFilter();
    return this.bucketRows().filter((f) => (cls === null || f.class === cls) && (st === null || f.state === st));
  });

  /** The outflow-versus-withdrawn summary (D2) — over the whole fetched bucket, not
   * the filtered view, so the summary always reads as the bucket's own overview
   * regardless of which slice is currently on screen. */
  protected readonly outflowCount = computed<number>(
    () => this.bucketRows().filter((f) => isFindingOutflow(f.state)).length,
  );
  protected readonly withdrawnCount = computed<number>(
    () => this.bucketRows().filter((f) => isFindingWithdrawn(f.state)).length,
  );

  private readonly proposalsQuery = injectHubGardenProposalsQuery();

  /** Every finding id an accepted-and-minted proposal names, mapped to that
   * proposal's own work-item pointer (D4) —
   * `gardening-proposals-page.ts`'s own `acceptedItemPointer` computed, generalized
   * from one proposal to every accepted-and-minted proposal in the docket at once. */
  private readonly findingWorkItemPointers = computed<ReadonlyMap<string, WorkItemPointer>>(() => {
    const map = new Map<string, WorkItemPointer>();
    for (const proposal of this.proposalsQuery.data() ?? []) {
      const closure = proposal.closure;
      if (closure?.closure !== 'accepted' || closure.item_outcome !== 'minted') continue;
      const pointer: WorkItemPointer = { source: closure.source!, ref: closure.ref! };
      for (const findingId of proposal.findings) map.set(findingId, pointer);
    }
    return map;
  });

  /** Every distinct pointer named above, deduplicated — {@link injectHubWorkItemsQuery}'s
   * own fan-out input, so two findings under the same proposal fan out to one read. */
  private readonly workItemPointers = computed<readonly WorkItemPointer[]>(() => {
    const byKey = new Map<string, WorkItemPointer>();
    for (const pointer of this.findingWorkItemPointers().values()) byKey.set(`${pointer.source}:${pointer.ref}`, pointer);
    return Array.from(byKey.values());
  });

  private readonly workItemsQuery = injectHubWorkItemsQuery(() => this.workItemPointers());

  private readonly workItemsByPointerKey = computed<ReadonlyMap<string, WorkItemView>>(
    () => new Map((this.workItemsQuery.data() ?? []).map((item) => [`${item.source}:${item.ref}`, item])),
  );

  /** The resolved work item for one finding id, or `null` while no accepted-and-
   * minted proposal names it, or while its own read hasn't resolved yet. `label`
   * falls back to the bare pointer, `gardening-proposals-page.ts`'s own
   * `workItemVm` fallback, since `WorkItemView.label` is itself nullable. */
  private workItemFor(findingId: string): ProposalWorkItemVm | null {
    const pointer = this.findingWorkItemPointers().get(findingId);
    if (pointer === undefined) return null;
    const item = this.workItemsByPointerKey().get(`${pointer.source}:${pointer.ref}`);
    if (item === undefined) return null;
    return { label: item.label ?? `${pointer.source}:${pointer.ref}`, webUrl: item.web_url ?? null };
  }

  protected readonly findingListRows = computed<readonly FindingListRowVm[]>(() =>
    this.filteredBucket().map((f) => ({
      findingId: f.finding_id,
      findingClass: f.class,
      locus: f.locus,
      summary: f.summary,
      introduced: f.introduced ?? null,
      lastSeenAt: f.last_seen_at,
      observedCount: f.observed_count,
      state: f.state,
      note: f.note ?? null,
      workItem: this.workItemFor(f.finding_id),
    })),
  );

  /** The bucket panel's own "nothing chosen yet" rest state, branched before ever
   * consulting `bucketQuery`'s own async state — `deltaState`'s own shape: the
   * bucket read is `enabled: false` while either routine or scope is `null`, which
   * reports `isPending()` forever otherwise. */
  protected readonly bucketState = computed<KitAsyncStateValue>(() =>
    this.selectedRoutine() === null || this.selectedScope() === null
      ? 'empty'
      : asyncState(this.bucketQuery, this.findingListRows().length === 0),
  );

  /** Whether the current identity may triage findings (`chunk:control`, D9) —
   * `null`/pending resolves to `false`, `gardening-proposals-page.ts`'s own
   * `canControl` shape. */
  protected readonly canControl = computed(() => hasPermission(this.meQuery.data(), 'chunk:control'));

  /** The bulk action the triage dialog is open against — `null` closes it. Only
   * the finding list's own `bulkTriage` output ever sets it. */
  protected readonly triagingBulkAction = signal<{ verb: FindingTriageVerb; findingIds: readonly string[] } | null>(
    null,
  );
}

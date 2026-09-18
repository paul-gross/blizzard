import { computed, type Signal } from '@angular/core';
import {
  confirmGoneFindingsMutationKey,
  FINDING_STATES,
  injectHubFindingsBucketQuery,
  injectHubRoutinesQuery,
  injectHubScopesQuery,
  injectPendingMutationVariables,
  notAFindingFindingsMutationKey,
  reopenFindingsMutationKey,
  resolveFindingsMutationKey,
  supersedeFindingsMutationKey,
  wontFixFindingsMutationKey,
  type AsyncStateQuery,
  type FindingExitVars,
  type FindingView,
  type KitChipOption,
  type RoutineView,
  type ScopeView,
} from 'fleet';

import { injectQueryFilters } from '../route-state';

const ALL_CLASSES = 'all';
const ALL_STATES = 'all';
/** UI-only chip sentinels for the "every routine"/"every scope" chip — never sent to
 * the server and never stored in the URL, where `null` is what actually rides both
 * the URL and the API call. Routine names and scope slugs are operator-authored,
 * exactly like `class` below, so they carry the same collision-guarding prefix
 * (review:F1). */
const ALL_ROUTINES = 'all';
const ALL_SCOPES = 'all';

/** `class` is opaque, deployment-chosen vocabulary — this prefix keeps a real class
 * literally named `all` from colliding with {@link ALL_CLASSES}. */
const CLASS_VALUE_PREFIX = 'class:';
/** Routine names are operator-authored, exactly like `class` above — this prefix
 * keeps a real routine literally named `all` from colliding with {@link ALL_ROUTINES}. */
const ROUTINE_VALUE_PREFIX = 'routine:';
/** Scope slugs are operator-authored, exactly like `class` above — this prefix
 * keeps a real scope literally named `all` from colliding with {@link ALL_SCOPES}. */
const SCOPE_VALUE_PREFIX = 'scope:';

export interface FindingsBucketFilters {
  readonly selectedRoutine: Signal<string | null>;
  readonly selectedScope: Signal<string | null>;
  readonly routineChips: Signal<readonly KitChipOption[]>;
  readonly routineChipValue: Signal<string>;
  readonly scopeChips: Signal<readonly KitChipOption[]>;
  readonly scopeChipValue: Signal<string>;
  onRoutineChoose(routine: string): void;
  onScopeChoose(scope: string): void;
  readonly classChips: Signal<readonly KitChipOption[]>;
  readonly classChipValue: Signal<string>;
  onClassChoose(value: string): void;
  readonly stateChips: readonly KitChipOption[];
  readonly stateFilter: Signal<string | null>;
  onStateChoose(value: string): void;
  readonly bucketQuery: AsyncStateQuery;
  readonly bucketRows: Signal<readonly FindingView[]>;
  readonly filteredBucket: Signal<readonly FindingView[]>;
}

/**
 * The findings triage bucket's routine/scope pair, class/state filters, and the
 * bucket read itself — split out of `gardening-findings-page.ts` purely to keep
 * that file under the lint's own line cap.
 *
 * All four filters live in the URL's query string (`route-state.ts`), not in
 * signals of their own: a filtered bucket is then a link the operator can send
 * somebody, and it survives every navigation this tab makes.
 *
 * The bucket widened to every routine and every scope (blizzard#486): its resting
 * state, with no query params at all, is "every routine, every scope" — `null`/`null`
 * — rather than a seeded pair. {@link selectedRoutine}/{@link selectedScope} read the
 * URL straight through with no fallback, since a `null` is now a fully meaningful
 * "all" state and not "nothing chosen yet". Routine and scope render as
 * {@link KitChipOption} rows, `classChips`/`stateChips`'s own shape, each now carrying
 * a leading "All" chip ({@link routineChipValue}/{@link scopeChipValue} map the `null`
 * filter state onto it, `classChipValue`'s own pattern) — there is no longer a
 * "requires a concrete pair" constraint pinning one of each selected at all times.
 */
export function injectFindingsBucketFilters(): FindingsBucketFilters {
  const url = injectQueryFilters();
  const routinesQuery = injectHubRoutinesQuery();
  const scopesQuery = injectHubScopesQuery();
  const routines = computed<readonly RoutineView[]>(() => routinesQuery.data() ?? []);
  const scopes = computed<readonly ScopeView[]>(() => scopesQuery.data() ?? []);

  const selectedRoutine = computed<string | null>(() => url.read('routine'));
  const selectedScope = computed<string | null>(() => url.read('scope'));

  const routineChips = computed<readonly KitChipOption[]>(() => [
    { value: ALL_ROUTINES, label: 'All routines', testid: 'gardening-findings-routine-all' },
    ...routines().map((r) => ({
      value: ROUTINE_VALUE_PREFIX + r.name,
      label: r.name,
      testid: `gardening-findings-routine-item-${r.name}`,
    })),
  ]);
  const routineChipValue = computed<string>(() => {
    const r = selectedRoutine();
    return r === null ? ALL_ROUTINES : ROUTINE_VALUE_PREFIX + r;
  });
  const scopeChips = computed<readonly KitChipOption[]>(() => [
    { value: ALL_SCOPES, label: 'All scopes', testid: 'gardening-findings-scope-all' },
    ...scopes().map((s) => ({
      value: SCOPE_VALUE_PREFIX + s.slug,
      label: s.slug,
      testid: `gardening-findings-scope-item-${s.slug}`,
    })),
  ]);
  const scopeChipValue = computed<string>(() => {
    const s = selectedScope();
    return s === null ? ALL_SCOPES : SCOPE_VALUE_PREFIX + s;
  });

  /** Each pick patches only its own URL param — no more pinning the other
   * dimension's current value alongside it (blizzard#486 retired the seeding chain
   * that pinning existed to keep coherent). It also clears the class/state filters
   * (F5), so a filter chosen against the old bucket can't strand the new one
   * looking empty with no active chip explaining why. */
  function onRoutineChoose(value: string): void {
    url.patch({
      routine: value === ALL_ROUTINES ? null : value.slice(ROUTINE_VALUE_PREFIX.length),
      class: null,
      state: null,
    });
  }
  function onScopeChoose(value: string): void {
    url.patch({
      scope: value === ALL_SCOPES ? null : value.slice(SCOPE_VALUE_PREFIX.length),
      class: null,
      state: null,
    });
  }

  const bucketQuery = injectHubFindingsBucketQuery(selectedRoutine, selectedScope);
  const bucketRows = computed<readonly FindingView[]>(() => bucketQuery.data() ?? []);

  const classFilter = computed<string | null>(() => url.read('class'));
  const stateFilter = computed<string | null>(() => url.read('state'));

  /** Every finding id any of the six human-driven triage verbs is currently pending
   * for (`bzh:frontend-pending-override`), paired with that verb's own known
   * resulting state — the exit five (`resolve`, `confirm-gone`, `wont-fix`,
   * `not-a-finding`, `supersede`) and `reopen`. Each fires the bulk
   * `FindingExitVars.findingIds` shape (`FindingSupersedeVars` too, which only adds a
   * field this read never touches), read by `mutationKey` alone
   * (`injectPendingMutationVariables`) rather than by owning any of the six mutations
   * here — the triage dialog that actually fires them (`gardening-finding-triage-
   * dialog.ts`) is a sibling surface this list never mounts.
   *
   * `derive_liveness` (`src/blizzard/hub/domain/findings.py`) folds a finding's facts
   * newest-wins, and every one of these six verbs' own fact `kind` is exactly its
   * resulting `state` (`reopened` folds to `"live"`, same as `add`/`observed`) — so
   * each verb's resulting state is fixed and known ahead of the call settling, the
   * same guarantee `chunk-detail.ts`'s `overrideStatus` documents for Pause/Complete.
   *
   * That does **not** make hiding the row while pending total on its own: the triage
   * surface (`finding-panel.ts`) renders every exit verb regardless of the finding's
   * *current* state, so an operator can re-dispatch a verb whose resulting state
   * equals the state it's already in — e.g. clicking Resolve again on an
   * already-`resolved` row while the active filter is `resolved`. That call never
   * moves the finding out of the filtered set, so hiding it would be a bare guess,
   * not a prediction — the plan's own totality rule (`bzh:frontend-pending-
   * override`) says fall back to no override there instead. The override is total
   * only relative to the currently active {@link stateFilter}: a finding is safely
   * hideable while pending exactly when the verb in flight for it resolves to some
   * state *other than* the one currently filtered on. {@link filteredBucket} below
   * computes that set itself, since it alone knows the active filter. */
  const resolvePending = injectPendingMutationVariables<FindingExitVars>(resolveFindingsMutationKey);
  const confirmGonePending = injectPendingMutationVariables<FindingExitVars>(confirmGoneFindingsMutationKey);
  const wontFixPending = injectPendingMutationVariables<FindingExitVars>(wontFixFindingsMutationKey);
  const notAFindingPending = injectPendingMutationVariables<FindingExitVars>(notAFindingFindingsMutationKey);
  const supersedePending = injectPendingMutationVariables<FindingExitVars>(supersedeFindingsMutationKey);
  const reopenPending = injectPendingMutationVariables<FindingExitVars>(reopenFindingsMutationKey);

  /** Each pending list above paired with the fixed state its own verb resolves to
   * (the doc comment above). Read together, rather than flattened into one bare id
   * set, so {@link filteredBucket} can compare each pending call's *own* resulting
   * state against the active {@link stateFilter} instead of assuming every pending
   * call is headed away from it. */
  const pendingByResultingState = computed<readonly { readonly findingIds: readonly string[]; readonly resultingState: string }[]>(
    () => [
      { findingIds: resolvePending().flatMap((v) => v.findingIds), resultingState: 'resolved' },
      { findingIds: confirmGonePending().flatMap((v) => v.findingIds), resultingState: 'gone-confirmed' },
      { findingIds: wontFixPending().flatMap((v) => v.findingIds), resultingState: 'wont-fix' },
      { findingIds: notAFindingPending().flatMap((v) => v.findingIds), resultingState: 'not-a-finding' },
      { findingIds: supersedePending().flatMap((v) => v.findingIds), resultingState: 'superseded' },
      { findingIds: reopenPending().flatMap((v) => v.findingIds), resultingState: 'live' },
    ],
  );

  const classChips = computed<readonly KitChipOption[]>(() => {
    const classes = Array.from(new Set(bucketRows().map((f) => f.class))).sort((a, b) => a.localeCompare(b));
    return [
      { value: ALL_CLASSES, label: 'All classes', testid: 'gardening-finding-class-all' },
      ...classes.map((c) => ({ value: CLASS_VALUE_PREFIX + c, label: c, testid: `gardening-finding-class-item-${c}` })),
    ];
  });
  const classChipValue = computed<string>(() => {
    const cls = classFilter();
    return cls === null ? ALL_CLASSES : CLASS_VALUE_PREFIX + cls;
  });
  function onClassChoose(value: string): void {
    url.patch({ class: value === ALL_CLASSES ? null : value.slice(CLASS_VALUE_PREFIX.length) });
  }

  /** {@link FINDING_STATES} is fixed, unlike `class`, so these carry no value
   * prefix and no collision guard. */
  const stateChips: readonly KitChipOption[] = [
    { value: ALL_STATES, label: 'All states', testid: 'gardening-finding-state-all' },
    ...FINDING_STATES.map((s) => ({ value: s, label: s, testid: `gardening-finding-state-item-${s}` })),
  ];
  function onStateChoose(value: string): void {
    url.patch({ state: value === ALL_STATES ? null : value });
  }

  /** Narrowed by class and state (D3, client-side) and now also by the pending-and-
   * hideable set below (`bzh:frontend-pending-override`) — but only inside the
   * `st !== null` branch: a concrete state chip is the only filter a pending triage
   * call could falsify, since "All states" already renders every finding regardless
   * of which state it reads. No cache write backs this — a rejected call reverts the
   * dropped row for free the instant its mutation's own `isPending()` clears.
   *
   * The hideable set is computed here, relative to `st`, rather than read off a flat
   * `pendingFindingIds` signal: an id is only safely hideable when the verb pending
   * for it resolves to a state *other than* `st` ({@link pendingByResultingState}'s
   * own doc comment) — a same-state re-dispatch (Resolve again on an already-
   * `resolved` row while filtered to `resolved`) never actually leaves the filtered
   * set, so it must stay visible throughout. */
  const filteredBucket = computed<readonly FindingView[]>(() => {
    const cls = classFilter();
    const st = stateFilter();
    const hideable =
      st === null
        ? new Set<string>()
        : new Set(
            pendingByResultingState()
              .filter((entry) => entry.resultingState !== st)
              .flatMap((entry) => entry.findingIds),
          );
    return bucketRows().filter((f) => {
      if (cls !== null && f.class !== cls) return false;
      if (st === null) return true;
      return f.state === st && !hideable.has(f.finding_id);
    });
  });

  return {
    selectedRoutine,
    selectedScope,
    routineChips,
    routineChipValue,
    scopeChips,
    scopeChipValue,
    onRoutineChoose,
    onScopeChoose,
    classChips,
    classChipValue,
    onClassChoose,
    stateChips,
    stateFilter,
    onStateChoose,
    bucketQuery,
    bucketRows,
    filteredBucket,
  };
}

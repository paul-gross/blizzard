import { computed, type Signal } from '@angular/core';
import {
  FINDING_STATES,
  injectHubFindingsBucketQuery,
  injectHubRoutinesQuery,
  injectHubScopesQuery,
  type AsyncStateQuery,
  type FindingView,
  type KitChipOption,
  type RoutineView,
  type ScopeView,
} from 'fleet';

import { injectQueryFilters } from '../route-state';

const ALL_CLASSES = 'all';
const ALL_STATES = 'all';

/** `class` is opaque, deployment-chosen vocabulary — this prefix keeps a real class
 * literally named `all` from colliding with {@link ALL_CLASSES}. */
const CLASS_VALUE_PREFIX = 'class:';

export interface FindingsBucketFilters {
  readonly selectedRoutine: Signal<string | null>;
  readonly selectedScope: Signal<string | null>;
  readonly routineChips: Signal<readonly KitChipOption[]>;
  readonly scopeChips: Signal<readonly KitChipOption[]>;
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
 * somebody, and it survives every navigation this tab makes. Reading them back
 * through `injectQueryFilters` also means the seed below is only ever a
 * *fallback* — a URL that names no routine still resolves to one.
 *
 * The pair is **persistent filter state independent of selection** — the routine
 * seeds from `firstRoutine` (the fetched routine list's own first row), and the
 * scope from whichever routine is *in effect*, URL-named or seeded, never from the
 * first row independently. That keeps the two halves coherent for a URL that names
 * only one of them: `?routine=weekly` resolves scope to weekly's own default, not
 * to the first row's. This tab mounts no run list of its own to borrow a
 * routine/scope pairing from (Findings used to share a tab with Runs, and seeded
 * from the run list's first row — that borrow broke once the two tabs split, so it
 * seeds off a routine's own declared default scope instead, the same pairing a run
 * of that routine would have used).
 *
 * Routine and scope render as {@link KitChipOption} rows, `classChips`/`stateChips`'s
 * own shape, but carry **no "All" option**: the bucket read
 * (`injectHubFindingsBucketQuery`) requires a concrete routine and a concrete
 * scope, there is no "every routine" read behind it, so one of each stays selected
 * at all times — {@link selectedRoutine}/{@link selectedScope} feed `selectedValue`
 * directly, always resolving to the seed until an explicit pick replaces it.
 */
export function injectFindingsBucketFilters(): FindingsBucketFilters {
  const url = injectQueryFilters();
  const routinesQuery = injectHubRoutinesQuery();
  const scopesQuery = injectHubScopesQuery();
  const routines = computed<readonly RoutineView[]>(() => routinesQuery.data() ?? []);
  const scopes = computed<readonly ScopeView[]>(() => scopesQuery.data() ?? []);

  const firstRoutine = computed<RoutineView | null>(() => routines()[0] ?? null);
  const defaultRoutine = computed<string | null>(() => firstRoutine()?.name ?? null);

  const selectedRoutine = computed<string | null>(() => url.read('routine') ?? defaultRoutine());

  /** The row {@link selectedRoutine} names, whether it got there from the URL or from
   * the seed — so the scope seed below follows the routine actually in effect rather
   * than the list's first row. */
  const selectedRoutineRow = computed<RoutineView | null>(() => {
    const name = selectedRoutine();
    return name === null ? null : (routines().find((r) => r.name === name) ?? null);
  });
  const defaultScope = computed<string | null>(() => selectedRoutineRow()?.default_scope_slug ?? null);

  const selectedScope = computed<string | null>(() => url.read('scope') ?? defaultScope());

  const routineChips = computed<readonly KitChipOption[]>(() =>
    routines().map((r) => ({ value: r.name, label: r.name, testid: `gardening-findings-routine-item-${r.name}` })),
  );
  const scopeChips = computed<readonly KitChipOption[]>(() =>
    scopes().map((s) => ({ value: s.slug, label: s.slug, testid: `gardening-findings-scope-item-${s.slug}` })),
  );

  /** Picking a scope pins the routine's current effective value alongside it (F2):
   * the routine seed is the fetched list's first row, so leaving it unnamed would
   * let the same link resolve to a different routine once the list grows.
   *
   * Picking a *routine* carries scope over only when the operator actually chose
   * one — `url.read`, not {@link selectedScope}. An explicit scope is a choice and
   * survives the pick; an unnamed scope is still sitting on the seed, and pinning
   * its value would staple the old routine's default onto the new routine, a
   * pairing nobody chose. Left unnamed, it re-seeds off the newly picked routine's
   * own `default_scope_slug` — the pairing a run of that routine would have used.
   *
   * Each pick also clears the class/state filters (F5), so a filter chosen against
   * the old bucket can't strand the new one looking empty with no active chip
   * explaining why. Both halves of that go out as one patch, so a pick is one
   * navigation. */
  function onRoutineChoose(routine: string): void {
    url.patch({ routine, scope: url.read('scope'), class: null, state: null });
  }
  function onScopeChoose(scope: string): void {
    url.patch({ routine: selectedRoutine(), scope, class: null, state: null });
  }

  const bucketQuery = injectHubFindingsBucketQuery(selectedRoutine, selectedScope);
  const bucketRows = computed<readonly FindingView[]>(() => bucketQuery.data() ?? []);

  const classFilter = computed<string | null>(() => url.read('class'));
  const stateFilter = computed<string | null>(() => url.read('state'));

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

  const filteredBucket = computed<readonly FindingView[]>(() => {
    const cls = classFilter();
    const st = stateFilter();
    return bucketRows().filter((f) => (cls === null || f.class === cls) && (st === null || f.state === st));
  });

  return {
    selectedRoutine,
    selectedScope,
    routineChips,
    scopeChips,
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

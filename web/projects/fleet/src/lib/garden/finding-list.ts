import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';

import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitButton } from '../kit/kit-button';
import { FleetWhen } from '../when-display';
import { isFindingExited, isFindingGoneFlagged } from './finding-state';
import type { ProposalWorkItemVm } from './proposal-panel';

/** One row of the findings triage bucket list (`hub finding list`'s own read) —
 * every field `FindingView` carries the triage surface needs, plus the accepted-
 * and-minted proposal's linked work item when one names this finding
 * (`ProposalWorkItemVm`'s own shape, `gardening-proposals-page.ts`'s evidence row
 * reuses the identical shape rather than this list inventing a second one).
 * `findingClass` renames the wire's `class` (`FindingView.class`), `RunDeltaVm`'s
 * own `AddedFindingView.class` → `findingClass` rename, so a template never
 * confuses it with the DOM `class` attribute. */
export interface FindingListRowVm {
  readonly findingId: string;
  readonly findingClass: string;
  readonly locus: string;
  readonly summary: string;
  readonly introduced: string | null;
  readonly lastSeenAt: string | null;
  readonly observedCount: number;
  readonly state: string;
  readonly note: string | null;
  readonly workItem: ProposalWorkItemVm | null;
}

/** The bulk-bar verbs the findings triage list can dispatch — every human-driven
 * exit `finding.mutations.ts` exposes, plus `reopen` (blizzard#402 Phase 3). Named
 * off the CLI's own verb spelling (`src/blizzard/hub/cli/finding.py`), so a
 * container routes an emitted verb straight to the matching mutation with no
 * translation table of its own. */
export type FindingTriageVerb = 'resolve' | 'confirm-gone' | 'wont-fix' | 'not-a-finding' | 'supersede' | 'reopen';

/** One bulk-bar action button, always offered (Reopen is handled separately,
 * gated on {@link FleetFindingList.canReopenSelection}). */
const BULK_ACTIONS: readonly { verb: FindingTriageVerb; label: string }[] = [
  { verb: 'resolve', label: 'Resolve' },
  { verb: 'confirm-gone', label: 'Confirm gone' },
  { verb: 'wont-fix', label: "Won't fix" },
  { verb: 'not-a-finding', label: 'Not a finding' },
  { verb: 'supersede', label: 'Supersede' },
];

/**
 * The gardening runs-and-findings tab's findings triage list (blizzard#402 Phases
 * 3-4) — presentational only, no query injection, `run-list.ts`'s own shape: renders
 * the rows it is handed, exactly as filtered by the container (D3: class/state
 * filtering happens client-side, this component stays dumb over whatever `rows()`
 * it's given).
 *
 * A row's own `state` decides its treatment (`finding-state.ts`'s own three-way
 * classification): still open with no flag renders plain; a `gone`-flagged row
 * (D8) renders tinted (`.fl-row--gone`) but stays a normal, fully rendered row —
 * `gone` is *not* exited; an exited row (one of `finding-state.ts`'s
 * `FINDING_EXIT_STATES`) renders dimmed (`.fl-row--exited`) but never leaves the
 * DOM. A row's own `note` (the run's own note on a `gone` row, or the exit note on
 * an exited one) renders directly on the row, never hidden behind a click.
 *
 * Multi-select and the bulk bar (Phase 3, D9) are gated on {@link canControl} —
 * `false` renders no checkbox and no bulk bar at all, `board-column.ts`'s own
 * checkbox-selection shape (a private `signal<ReadonlySet<string>>`, an
 * `isSelected`/`toggle` pair, and an ordered `selectedIds` computed). This
 * component owns *selection*; it does not decide what an action means — every
 * bulk-bar button only emits {@link bulkTriage} with its own verb and the
 * currently selected ids, leaving the container to open whatever dialog the verb
 * needs (D4).
 */
@Component({
  selector: 'fleet-finding-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, FleetWhen, KitButton],
  templateUrl: './finding-list.html',
  styleUrl: './finding-list.css',
})
export class FleetFindingList {
  readonly rows = input.required<readonly FindingListRowVm[]>();
  readonly state = input.required<KitAsyncStateValue>();

  /** Whether the current identity may triage findings (`chunk:control`, D9) —
   * `false` withholds every checkbox and the bulk bar outright, `proposal-
   * panel.ts`'s own `canControl` gating. */
  readonly canControl = input(false);

  /** Emitted with a verb and the ids it applies to when a bulk-bar button is
   * activated — the container decides what the verb means (D4), this component
   * only reports the selection at the moment of the click. */
  readonly bulkTriage = output<{ verb: FindingTriageVerb; findingIds: readonly string[] }>();

  protected readonly bulkActions = BULK_ACTIONS;

  /** Finding ids checked for a bulk action. */
  private readonly selection = signal<ReadonlySet<string>>(new Set());

  protected isGone(row: FindingListRowVm): boolean {
    return isFindingGoneFlagged(row.state);
  }

  protected isExited(row: FindingListRowVm): boolean {
    return isFindingExited(row.state);
  }

  protected isSelected(findingId: string): boolean {
    return this.selection().has(findingId);
  }

  protected toggle(findingId: string): void {
    this.selection.update((prev) => {
      const next = new Set(prev);
      if (next.has(findingId)) next.delete(findingId);
      else next.add(findingId);
      return next;
    });
  }

  /** Checked ids in the order they appear in {@link rows} — `board-column.ts`'s
   * own `selectedIds` shape. */
  protected readonly selectedIds = computed<readonly string[]>(() => {
    const selected = this.selection();
    return this.rows()
      .map((row) => row.findingId)
      .filter((findingId) => selected.has(findingId));
  });

  /** Whether every row currently in view is selected — drives the header
   * checkbox's `checked` state and what clicking it does next. */
  protected readonly allVisibleSelected = computed<boolean>(() => {
    const rows = this.rows();
    return rows.length > 0 && rows.every((row) => this.selection().has(row.findingId));
  });

  /** Whether some, but not all, of the rows currently in view are selected —
   * drives the header checkbox's `indeterminate` state. */
  protected readonly someVisibleSelected = computed<boolean>(() => {
    const rows = this.rows();
    const selectedCount = rows.filter((row) => this.selection().has(row.findingId)).length;
    return selectedCount > 0 && selectedCount < rows.length;
  });

  /** The header checkbox, activated — selects every row currently in view, or
   * clears the whole selection when every visible row is already selected. */
  protected toggleSelectAll(): void {
    if (this.allVisibleSelected()) {
      this.selection.set(new Set());
      return;
    }
    this.selection.set(new Set(this.rows().map((row) => row.findingId)));
  }

  /** Whether Reopen belongs in the bulk bar right now — only when at least one
   * row is selected and every one of them has exited (`isFindingExited`);
   * reopening a still-open (`live` or `gone`-flagged) finding isn't a real
   * action, so the button doesn't render at all otherwise. */
  protected readonly canReopenSelection = computed<boolean>(() => {
    const selected = this.selection();
    if (selected.size === 0) return false;
    const selectedRows = this.rows().filter((row) => selected.has(row.findingId));
    return selectedRows.length > 0 && selectedRows.every((row) => this.isExited(row));
  });

  protected emitBulk(verb: FindingTriageVerb): void {
    this.bulkTriage.emit({ verb, findingIds: this.selectedIds() });
  }
}

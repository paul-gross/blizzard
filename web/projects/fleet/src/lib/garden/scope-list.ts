import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitButton } from '../kit/kit-button';

/** One row of the scope list — slug, description, and retired state (AC 1). */
export interface ScopeRowVm {
  readonly slug: string;
  readonly description: string;
  readonly retired: boolean;
}

/** Emitted when the operator sets a scope's description in place (AC 2). */
export interface ScopeDescriptionEditEvent {
  readonly slug: string;
  readonly description: string;
}

/**
 * The routines panel's scope list (blizzard#400) — every scope, retired ones
 * included and marked as such (AC 5), each with an in-place description editor and a
 * retire/re-enable control naming the CLI verb behind it (AC 3, AC 8). Presentational
 * only: no query injection, `FleetRoutineList`'s own shape — rows in,
 * `editDescription`/`retire`/`enable` events out, the container's mutations decide
 * what they do. Retire/enable confirm before emitting, `GraphDetailLifecycle`'s own
 * confirm-then-emit pattern.
 */
@Component({
  selector: 'fleet-scope-list',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitButton],
  templateUrl: './scope-list.html',
  styleUrl: './scope-list.css',
})
export class FleetScopeList {
  readonly rows = input.required<readonly ScopeRowVm[]>();
  readonly state = input.required<KitAsyncStateValue>();

  /** Whether the current identity may author scopes (`graph:edit` — the same
   * permission `src/blizzard/hub/api/scopes.py` requires of every write route).
   * Withholds the description editor and the retire/enable controls when `false`. */
  readonly canEdit = input(false);

  readonly editDescription = output<ScopeDescriptionEditEvent>();
  readonly retire = output<string>();
  readonly enable = output<string>();

  /** Emit a description edit — no-op on a blank value (`ChunkFacts.submitGraph`'s own
   * guard). */
  protected submitDescription(slug: string, description: string): void {
    const trimmed = description.trim();
    if (!trimmed) return;
    this.editDescription.emit({ slug, description: trimmed });
  }

  /** Confirm, then emit `retire` for the container's mutation to fire. */
  protected onRetire(slug: string): void {
    const confirmed = globalThis.confirm(
      `Retire scope ${slug}? It is removed from every picker; its findings stay live, queryable, and attributable.`,
    );
    if (!confirmed) return;
    this.retire.emit(slug);
  }

  /** Confirm, then emit `enable` for the container's mutation to fire. */
  protected onEnable(slug: string): void {
    const confirmed = globalThis.confirm(`Re-enable scope ${slug}? It resumes appearing in every picker.`);
    if (!confirmed) return;
    this.enable.emit(slug);
  }
}

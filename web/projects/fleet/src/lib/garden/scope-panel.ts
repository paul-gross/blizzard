import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitButton } from '../kit/kit-button';
import type { ScopeDescriptionEditEvent } from './scope-list';

/** The selected scope's whole panel view model — plain data, `RoutinePanelVm`'s own
 * shape (`routine-panel.ts`). `defaultingRoutineNames` is free: the container already
 * holds the routines query the routine list itself reads. */
export interface ScopePanelVm {
  readonly slug: string;
  readonly description: string;
  readonly retired: boolean;
  /** Every routine whose own `defaultScopeSlug` names this scope, by name. */
  readonly defaultingRoutineNames: readonly string[];
}

/**
 * The gardening scope panel's single-scope detail — the description (in-place
 * editable when `canEdit`, else plain text), retire/re-enable, and the routines
 * that default to this scope. Presentational only: it renders exactly the view
 * model it is handed and injects no query (`FleetRoutinePanel`'s own shape).
 *
 * Retire/enable confirm before emitting — `FleetScopeList`'s own confirm-then-emit
 * pattern, carried over onto this panel now that the list is selection-only.
 */
@Component({
  selector: 'fleet-scope-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitButton],
  templateUrl: './scope-panel.html',
  styleUrl: './scope-panel.css',
})
export class FleetScopePanel {
  readonly vm = input<ScopePanelVm | null>(null);
  readonly state = input.required<KitAsyncStateValue>();

  /** Whether the current identity may author scopes (`graph:edit`) — withholds the
   * description editor and the retire/enable controls when `false`. */
  readonly canEdit = input(false);

  /** Set on a failed edit/retire/enable; rendered beside the controls that raise it. */
  readonly actionError = input<string | null>(null);

  readonly editDescription = output<ScopeDescriptionEditEvent>();
  readonly retire = output<string>();
  readonly enable = output<string>();

  /** Emit a description edit — no-op on a blank value (`FleetScopeList`'s own
   * guard). */
  protected submitDescription(description: string): void {
    const trimmed = description.trim();
    const slug = this.vm()?.slug;
    if (!trimmed || slug === undefined) return;
    this.editDescription.emit({ slug, description: trimmed });
  }

  /** Confirm, then emit `retire` for the container's mutation to fire. */
  protected onRetire(): void {
    const slug = this.vm()?.slug;
    if (slug === undefined) return;
    const confirmed = globalThis.confirm(
      `Retire scope ${slug}? It is removed from every picker; its findings stay live, queryable, and attributable.`,
    );
    if (!confirmed) return;
    this.retire.emit(slug);
  }

  /** Confirm, then emit `enable` for the container's mutation to fire. */
  protected onEnable(): void {
    const slug = this.vm()?.slug;
    if (slug === undefined) return;
    const confirmed = globalThis.confirm(`Re-enable scope ${slug}? It resumes appearing in every picker.`);
    if (!confirmed) return;
    this.enable.emit(slug);
  }
}

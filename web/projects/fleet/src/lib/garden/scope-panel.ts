import { ChangeDetectionStrategy, Component, input, output, signal } from '@angular/core';

import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitBadge } from '../kit/kit-badge';
import { KitButton } from '../kit/kit-button';
import { KitConfirmDialog } from '../kit/kit-confirm-dialog';
import type { ScopeDescriptionEditEvent } from './scope-list';

/** One routine related to the selected scope — `isDefault` marks whether this scope
 * is that routine's own default, `RelatedScopeVm`'s own shape (`routine-panel.ts`). */
export interface RelatedRoutineVm {
  readonly name: string;
  readonly isDefault: boolean;
}

/** The selected scope's whole panel view model — plain data, `RoutinePanelVm`'s own
 * shape (`routine-panel.ts`). `relatedRoutines` is `null` until its own read resolves
 * (`RoutinePanelVm.trend`'s own nullable-secondary-read shape) — a section whose data
 * is still `null` is absent rather than rendering as settled-empty. */
export interface ScopePanelVm {
  readonly slug: string;
  readonly description: string;
  readonly retired: boolean;
  /** Every routine linked to this scope, each marked whether it defaults here (D4)
   * — `null` while the relation read is still pending. */
  readonly relatedRoutines: readonly RelatedRoutineVm[] | null;
}

/**
 * The gardening scope panel's single-scope detail — the description (in-place
 * editable when `canEdit`, else plain text), retire/re-enable, and the routines
 * related to this scope, each marked whether it defaults here. Presentational
 * only: it renders exactly the view model it is handed and injects no query
 * (`FleetRoutinePanel`'s own shape).
 *
 * Retire/enable confirm before emitting — `FleetScopeList`'s own confirm-then-emit
 * pattern, carried over onto this panel now that the list is selection-only.
 */
@Component({
  selector: 'fleet-scope-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitBadge, KitButton, KitConfirmDialog],
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

  /** Whether the edit-description mutation is in flight — disables the Set button. */
  readonly editPending = input(false);

  /** Whether the retire/enable mutation is in flight — disables both Re-enable and
   * Retire, since only one is ever shown for this scope's current lifecycle state. */
  readonly lifecyclePending = input(false);

  readonly editDescription = output<ScopeDescriptionEditEvent>();
  readonly retire = output<string>();
  readonly enable = output<string>();

  protected readonly pendingConfirm = signal<{
    readonly heading: string;
    readonly message: string;
    readonly confirmLabel: string;
    readonly variant: 'primary' | 'danger';
    readonly run: () => void;
  } | null>(null);

  /** Emit a description edit — no-op on a blank value (`FleetScopeList`'s own
   * guard). */
  protected submitDescription(description: string): void {
    const trimmed = description.trim();
    const slug = this.vm()?.slug;
    if (!trimmed || slug === undefined) return;
    this.editDescription.emit({ slug, description: trimmed });
  }

  /** Open a confirmation before emitting `retire` for the container's mutation to fire. */
  protected onRetire(): void {
    const slug = this.vm()?.slug;
    if (slug === undefined) return;
    this.pendingConfirm.set({
      heading: `Retire scope ${slug}`,
      message: `Retire scope ${slug}? It is removed from every picker; its findings stay live, queryable, and attributable.`,
      confirmLabel: 'Retire',
      variant: 'danger',
      run: () => this.retire.emit(slug),
    });
  }

  /** Open a confirmation before emitting `enable` for the container's mutation to fire. */
  protected onEnable(): void {
    const slug = this.vm()?.slug;
    if (slug === undefined) return;
    this.pendingConfirm.set({
      heading: `Re-enable scope ${slug}`,
      message: `Re-enable scope ${slug}? It resumes appearing in every picker.`,
      confirmLabel: 'Re-enable',
      variant: 'primary',
      run: () => this.enable.emit(slug),
    });
  }

  protected onConfirmed(): void {
    const pending = this.pendingConfirm();
    this.pendingConfirm.set(null);
    pending?.run();
  }

  protected onCancelled(): void {
    this.pendingConfirm.set(null);
  }
}

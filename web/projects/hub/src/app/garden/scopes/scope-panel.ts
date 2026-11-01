import { ChangeDetectionStrategy, Component, type TemplateRef, effect, input, output, signal, untracked } from '@angular/core';
import { RouterLink } from '@angular/router';

import {
  FleetWhen,
  KitAsyncState,
  type KitAsyncStateValue,
  KitBadge,
  KitButton,
  KitConfirmDialog,
  type KitFact,
  KitFactList,
  KitTextInput,
  KitErrorLine,
} from 'fleet';
import { LensLink } from 'fleet/shell';

import { lifecycleTone } from '../../core/lifecycle-tone';
import type { ScopeDescriptionEditEvent, ScopeSweptVm } from './scope-list';

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
  /** The scope's real, unoverridden lifecycle flag — which control renders
   * (Retire/Re-enable) stays keyed off this, never off {@link renderedRetired}
   * (`graph-detail-header.ts`'s own `retired`/`renderedRetired` split), so a
   * pending lifecycle mutation's own predicted outcome can never flip which verb the
   * next click would fire. */
  readonly retired: boolean;
  /** The lifecycle badge's rendered value — which may be a pending Retire/Enable's
   * predicted `retired` flag rather than the real {@link retired}
   * (`bzh:frontend-pending-override`). */
  readonly renderedRetired: boolean;
  /** Every routine linked to this scope, each marked whether it defaults here
   * — `null` while the relation read is still pending. */
  readonly relatedRoutines: readonly RelatedRoutineVm[] | null;
  /** When a run in the runs window last swept this scope — `null` while the runs read is pending. */
  readonly swept: ScopeSweptVm | null;
}

/**
 * The gardening scope panel's single-scope detail — a header holding the slug, its project chip and lifecycle, with
 * Edit and Retire (or Re-enable) at its right; the description and when the scope was last swept as a fact list; the
 * routines that tend it, each a link to that routine and marked whether it defaults here; and a callout for each other
 * project holding a scope of the same slug. Presentational only: it renders exactly the view model it is handed and
 * injects no query.
 *
 * Edit opens the description in place; Set emits the edit, and the editor closes once the new description reads back. Retire and re-enable confirm, then emit.
 */
@Component({
  selector: 'app-scope-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitAsyncState, KitBadge, KitButton, KitConfirmDialog, KitFactList, KitTextInput, LensLink, RouterLink, KitErrorLine],
  templateUrl: './scope-panel.html',
  styleUrl: './scope-panel.css',
})
export class FleetScopePanel {
  readonly vm = input<ScopePanelVm | null>(null);
  readonly state = input.required<KitAsyncStateValue>();

  /** The project the scope belongs to, shown beside its slug; `null` until read. */
  readonly project = input<string | null>(null);

  /** The other projects holding a scope of the same slug — different records that share a name. */
  readonly sameSlugIn = input<readonly string[]>([]);

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

  /** The lifecycle badge's tone, from the hub's one lifecycle owner. */
  protected readonly lifecycleTone = lifecycleTone;

  /** The scope whose description is open for editing, with the description it opened on and the draft typed so far —
   * `null` while none is. Keyed by slug so a pick of another scope never carries an open editor, or its draft, across,
   * and by the description so a saved edit, reading back a new one, closes the editor while a failed one leaves it open
   * beside its error. */
  private readonly editing = signal<{
    readonly slug: string;
    readonly original: string;
    readonly value: string;
  } | null>(null);

  /** Picking another scope closes the editor and drops its draft, so coming back finds the saved description. */
  private readonly closeOnPick = effect(() => {
    const slug = this.vm()?.slug;
    untracked(() => {
      if (this.editing()?.slug !== slug) this.editing.set(null);
    });
  });

  protected isEditing(panel: ScopePanelVm): boolean {
    const editing = this.editing();
    return editing?.slug === panel.slug && editing.original === panel.description;
  }

  protected startEditing(panel: ScopePanelVm): void {
    this.editing.set({ slug: panel.slug, original: panel.description, value: panel.description });
  }

  protected stopEditing(): void {
    this.editing.set(null);
  }

  /** The record's facts — the description and the last sweep, each markup rendered from the view's own templates. */
  protected factRows(description: TemplateRef<unknown>, lastSwept: TemplateRef<unknown>): readonly KitFact[] {
    return [
      { label: 'Description', template: description, testid: 'gardening-scope-panel-description' },
      { label: 'Last swept', template: lastSwept, testid: 'gardening-scope-panel-last-swept' },
    ];
  }

  protected descriptionValue(panel: ScopePanelVm): string {
    return this.isEditing(panel) ? (this.editing()?.value ?? panel.description) : panel.description;
  }

  protected updateDescription(panel: ScopePanelVm, value: string): void {
    this.editing.set({ slug: panel.slug, original: panel.description, value });
  }

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

  /** Open a confirmation, and emit `retire` once the operator confirms. */
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

  /** Open a confirmation, and emit `enable` once the operator confirms. */
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

import { ChangeDetectionStrategy, Component, type TemplateRef, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import {
  FleetWhen,
  KitAsyncState,
  type KitAsyncStateValue,
  KitBadge,
  KitButton,
  KitConfirmDialog,
  KitErrorLine,
  type KitFact,
  KitFactList,
  type Tone,
} from 'fleet';
import { LensLink } from 'fleet/shell';

import { lifecycleTone } from '../../core/lifecycle-tone';
import { type CadenceDraft, FleetCadenceForm } from './cadence-form';
import { type ChoreDraft, FleetChoreForm } from './chore-form';

/** One thing stopping the cadence's errands: the chore (`null` for the whole cadence), the scope, and why. */
export interface AttentionVm {
  readonly chore: string | null;
  readonly scope: string | null;
  readonly reason: string;
}

/** One errand in a chore's table. */
export interface ErrandRowVm {
  readonly scope: string;
  readonly label: string;
  readonly tone: Tone;
  /** The chunk holding the last run's work, linked to its run detail — `null` with no last run or none resolved. */
  readonly lastRunChunkId: string | null;
  /** When the last run was scheduled, `null` with none. */
  readonly lastRunAt: string | null;
  /** When the errand is next due, `null` while running or stopped. */
  readonly dueAt: string | null;
  /** Why attention stops it, empty when nothing does. */
  readonly attention: string;
}

/** One chore of the cadence, with its errands — `null` while the errands read is pending. */
export interface ChorePanelVm {
  readonly key: string;
  readonly routine: string;
  readonly scopes: string;
  readonly mode: string;
  readonly promote: boolean;
  readonly note: string | null;
  /** The chore as its edit form opens. */
  readonly draft: ChoreDraft;
  readonly errands: readonly ErrandRowVm[] | null;
}

/** One upcoming moment: its instant and that instant as full local text. */
export interface MomentVm {
  readonly iso: string;
  readonly text: string;
}

/** The selected cadence's whole panel view model. */
export interface CadencePanelVm {
  readonly cadenceId: string;
  readonly slug: string;
  readonly name: string;
  /** The revision every edit is sent against. */
  readonly revision: number;
  /** The real lifecycle flag — which of Retire and Re-enable shows keys off it, never off {@link renderedRetired}. */
  readonly retired: boolean;
  /** The lifecycle badge's value — a pending retire or enable's predicted flag while one is in flight. */
  readonly renderedRetired: boolean;
  readonly rhythm: string;
  /** The next five moments the schedule fires, earliest first — empty with no schedule or when retired. */
  readonly nextMoments: readonly MomentVm[];
  readonly attention: readonly AttentionVm[];
  readonly chores: readonly ChorePanelVm[];
  /** The cadence as its edit form opens. */
  readonly draft: CadenceDraft;
}

/** A chore edit: the chore's key as served, and its new draft. */
export interface ChoreEditEvent {
  readonly key: string;
  readonly draft: ChoreDraft;
}

/** Which form is open, on which cadence, at which revision — a form reading back a new revision has saved, and closes. */
interface OpenForm {
  readonly cadenceId: string;
  readonly revision: number;
  readonly form: 'cadence' | 'add-chore' | { readonly chore: string };
}

/** The empty chore a new one opens on. */
const NEW_CHORE: ChoreDraft = { key: '', routine: '', scopes: 'all', mode: 'full', promote: true, note: '' };

/**
 * The gardening cadence panel — a header with the cadence's name, slug and lifecycle, and Edit and Retire (or
 * Re-enable) at its right; its rhythm and next five moments; what stops its errands; and each chore with its errands
 * over each scope, each chore editable and removable, and a chore added at the foot. Presentational: it renders the
 * view model it is handed and emits each gesture; the caller sends it.
 *
 * A form stays open until the cadence reads back at a new revision, so a refused save leaves it open beside its error.
 */
@Component({
  selector: 'app-cadence-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    FleetCadenceForm,
    FleetChoreForm,
    FleetWhen,
    KitAsyncState,
    KitBadge,
    KitButton,
    KitConfirmDialog,
    KitErrorLine,
    KitFactList,
    LensLink,
    RouterLink,
  ],
  templateUrl: './cadence-panel.html',
  styleUrl: './cadence-panel.css',
})
export class FleetCadencePanel {
  readonly vm = input<CadencePanelVm | null>(null);
  readonly state = input.required<KitAsyncStateValue>();

  /** Whether the current identity may author cadences (`graph:edit`); withholds every control when `false`. */
  readonly canEdit = input(false);
  /** Set on a failed edit, retire, or enable; rendered beside the controls that raise it. */
  readonly actionError = input<string | null>(null);
  /** Whether an edit of this cadence is in flight — disables every save and remove. */
  readonly editPending = input(false);
  /** Whether a retire or enable of this cadence is in flight. */
  readonly lifecyclePending = input(false);

  readonly edit = output<CadenceDraft>();
  readonly addChore = output<ChoreDraft>();
  readonly editChore = output<ChoreEditEvent>();
  readonly removeChore = output<string>();
  readonly retire = output<void>();
  readonly enable = output<void>();

  protected readonly lifecycleTone = lifecycleTone;
  protected readonly newChore = NEW_CHORE;

  private readonly open = signal<OpenForm | null>(null);

  protected readonly pendingConfirm = signal<{
    readonly heading: string;
    readonly message: string;
    readonly confirmLabel: string;
    readonly variant: 'primary' | 'danger';
    readonly run: () => void;
  } | null>(null);

  /** Whether `form` is open on `panel` as it now reads. */
  protected isOpen(panel: CadencePanelVm, form: 'cadence' | 'add-chore' | string): boolean {
    const open = this.open();
    if (open === null || open.cadenceId !== panel.cadenceId || open.revision !== panel.revision) return false;
    return typeof open.form === 'string' ? open.form === form : open.form.chore === form;
  }

  protected openForm(panel: CadencePanelVm, form: OpenForm['form']): void {
    this.open.set({ cadenceId: panel.cadenceId, revision: panel.revision, form });
  }

  protected closeForm(): void {
    this.open.set(null);
  }

  /** The record's facts — its rhythm and its next moments. */
  protected factRows(moments: TemplateRef<unknown>, rhythm: string): readonly KitFact[] {
    return [
      { label: 'Rhythm', value: rhythm, testid: 'gardening-cadence-panel-rhythm' },
      { label: 'Next', template: moments, testid: 'gardening-cadence-panel-next' },
    ];
  }

  protected onRetire(panel: CadencePanelVm): void {
    this.confirm({
      heading: `Retire cadence ${panel.slug}`,
      message: `Retire cadence ${panel.slug}? It mints nothing until it is re-enabled, and its errands all stop.`,
      confirmLabel: 'Retire',
      variant: 'danger',
      run: () => this.retire.emit(),
    });
  }

  protected onEnable(panel: CadencePanelVm): void {
    this.confirm({
      heading: `Re-enable cadence ${panel.slug}`,
      message: `Re-enable cadence ${panel.slug}? Its errands come due on its rhythm again.`,
      confirmLabel: 'Re-enable',
      variant: 'primary',
      run: () => this.enable.emit(),
    });
  }

  protected onRemoveChore(panel: CadencePanelVm, key: string): void {
    this.confirm({
      heading: `Remove chore ${key}`,
      message: `Remove chore ${key} from cadence ${panel.slug}? Its errands go with it; the runs it minted stay.`,
      confirmLabel: 'Remove',
      variant: 'danger',
      run: () => this.removeChore.emit(key),
    });
  }

  private confirm(prompt: NonNullable<ReturnType<typeof this.pendingConfirm>>): void {
    this.pendingConfirm.set(prompt);
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

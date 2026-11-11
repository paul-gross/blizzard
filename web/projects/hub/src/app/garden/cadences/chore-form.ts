import { ChangeDetectionStrategy, Component, computed, input, linkedSignal, output } from '@angular/core';

import { type KitChipOption, type UrgencyView, KitButton, KitCheckbox, KitChips, KitErrorLine, KitSelect, KitTextInput } from 'fleet';

import { choreModeLabel } from './cadence-vocabulary';

/** A chore as its form states it, every field as typed. */
export interface ChoreDraft {
  readonly key: string;
  /** The routine's slug. */
  readonly routine: string;
  /** `all`, a comma- or space-separated list of scope slugs, or blank for the routine's default scope. */
  readonly scopes: string;
  /** The run mode — kept verbatim, a mode this build does not know included. */
  readonly mode: string;
  readonly promote: boolean;
  readonly note: string;
  readonly urgency?: string | null;
}

/**
 * One chore as a form — adding a chore, or editing one in place. It holds only the draft typed so far, reopening on
 * {@link initial}; Save emits the draft. A mode this build does not know is offered as itself, so saving an untouched
 * chore never rewrites it. Presentational: no query injection.
 */
@Component({
  selector: 'app-chore-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitCheckbox, KitChips, KitErrorLine, KitSelect, KitTextInput],
  templateUrl: './chore-form.html',
  styleUrl: './chore-form.css',
})
export class FleetChoreForm {
  readonly initial = input.required<ChoreDraft>();
  readonly urgencies = input<readonly UrgencyView[]>([]);
  /** Whether the key may be typed — a chore keeps its key once written, so an edit fixes it. */
  readonly keyEditable = input(true);
  readonly pending = input(false);
  readonly error = input<string | null>(null);
  readonly testid = input('gardening-chore-form');

  readonly saved = output<ChoreDraft>();
  readonly cancelled = output<void>();

  protected readonly draft = linkedSignal(() => this.initial());
  protected readonly urgencyOptions = computed(() => [
    { value: '', label: 'Routine default' },
    ...this.urgencies().filter((u) => !u.retired || u.slug === this.draft().urgency || u.urgency_id === this.draft().urgency).map((u) => ({ value: u.slug, label: u.name })),
  ]);

  protected readonly urgencyValue = computed(() => {
    const value = this.draft().urgency ?? '';
    return this.urgencies().find((u) => u.urgency_id === value)?.slug ?? value;
  });

  /** The known modes, and the draft's own when this build does not know it. */
  protected readonly modeOptions = computed<readonly KitChipOption[]>(() => {
    const known = choreModeLabel.keys.map((mode) => ({ value: mode, label: choreModeLabel(mode) }));
    const mode = this.initial().mode;
    return choreModeLabel.has(mode) ? known : [...known, { value: mode, label: mode }];
  });

  protected set(field: 'key' | 'routine' | 'scopes' | 'mode' | 'note' | 'urgency', value: string): void {
    this.draft.update((draft) => ({ ...draft, [field]: value }));
  }

  protected setPromote(promote: boolean): void {
    this.draft.update((draft) => ({ ...draft, promote }));
  }

  protected save(): void {
    this.saved.emit(this.draft());
  }
}

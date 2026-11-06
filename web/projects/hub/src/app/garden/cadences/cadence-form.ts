import { ChangeDetectionStrategy, Component, input, linkedSignal, output } from '@angular/core';

import { type KitChipOption, KitButton, KitErrorLine, KitSelect, KitTextInput } from 'fleet';

import { CADENCE_ANCHORS } from './cadence-vocabulary';

/** A cadence as its form states it, every field as typed. A blank field is left unstated. */
export interface CadenceDraft {
  readonly slug: string;
  readonly name: string;
  readonly cron: string;
  readonly zone: string;
  /** A whole number and `m`, `h`, or `d` — `10d`. */
  readonly spacing: string;
  /** `scheduled`, `started`, `finished`, or blank. */
  readonly anchor: string;
}

const ANCHOR_OPTIONS: readonly KitChipOption[] = [
  { value: '', label: 'unstated (started)' },
  ...CADENCE_ANCHORS.map((anchor) => ({ value: anchor, label: anchor })),
];

/**
 * A cadence's name and rhythm as a form — the create form, which also takes the slug, or the edit form, where the slug
 * is fixed. It holds only the draft typed so far, reopening on {@link initial}; Save emits the draft and the caller
 * judges it. Presentational: no query injection.
 */
@Component({
  selector: 'app-cadence-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitErrorLine, KitSelect, KitTextInput],
  templateUrl: './cadence-form.html',
  styleUrl: './cadence-form.css',
})
export class FleetCadenceForm {
  readonly initial = input.required<CadenceDraft>();
  /** Whether the form creates a cadence — only then is the slug typed. */
  readonly creating = input(false);
  readonly pending = input(false);
  readonly error = input<string | null>(null);
  /** The prefix every field's test id carries. */
  readonly testid = input('gardening-cadence-form');

  readonly saved = output<CadenceDraft>();
  readonly cancelled = output<void>();

  protected readonly anchorOptions = ANCHOR_OPTIONS;

  protected readonly draft = linkedSignal(() => this.initial());

  protected set(field: keyof CadenceDraft, value: string): void {
    this.draft.update((draft) => ({ ...draft, [field]: value }));
  }

  protected save(): void {
    this.saved.emit(this.draft());
  }
}

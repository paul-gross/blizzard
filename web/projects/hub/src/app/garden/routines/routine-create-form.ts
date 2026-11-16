import { ChangeDetectionStrategy, Component, computed, input, output, signal } from '@angular/core';
import { KitButton, KitErrorLine, KitSelect, KitTextInput, type UrgencyView } from 'fleet';
import { urgencyOptions } from '../../core/urgency-options.model';

export interface RoutineCreateDraft {
  readonly slug: string;
  readonly name: string;
  readonly graph: string;
  readonly scope: string;
  readonly urgency: string;
}

@Component({
  selector: 'app-routine-create-form',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitErrorLine, KitSelect, KitTextInput],
  templateUrl: './routine-create-form.html',
  styleUrl: './routine-create-form.css',
})
export class RoutineCreateForm {
  readonly urgencies = input<readonly UrgencyView[]>([]);
  readonly pending = input(false);
  readonly error = input<string | null>(null);
  readonly saved = output<RoutineCreateDraft>();
  readonly cancelled = output<void>();
  protected readonly draft = signal<RoutineCreateDraft>({ slug: '', name: '', graph: '', scope: '', urgency: '' });
  protected readonly options = computed(() =>
    urgencyOptions(this.urgencies(), {
      key: 'slug',
      retired: 'exclude',
      inherit: { label: 'Tenant default', name: this.urgencies().find((u) => u.default)?.name ?? null },
    }));
  protected set(field: keyof RoutineCreateDraft, value: string): void {
    this.draft.update((draft) => ({ ...draft, [field]: value }));
  }
}

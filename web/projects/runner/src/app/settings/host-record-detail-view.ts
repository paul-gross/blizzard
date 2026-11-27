import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { type KitFact, KitButton, KitErrorLine, KitFactList } from 'fleet/kit';

import type { HostRecordVm } from './host-records.model';

/** One record's detail: every key its table states, then Edit, and Retire or Enable, as the session is offered
 * them, with a refused verb's reason on its line; the record's history is projected beneath. Presentational. */
@Component({
  selector: 'app-host-record-detail-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton, KitErrorLine, KitFactList],
  templateUrl: './host-record-detail-view.html',
  styleUrl: './host-record-detail-view.css',
})
export class HostRecordDetailView {
  /** The prefix of the detail's test ids: `<prefix>-detail`, `<prefix>-edit`, … */
  readonly prefix = input.required<string>();
  readonly record = input.required<HostRecordVm>();
  /** Each verb's control held off while that verb alone is in flight. */
  readonly editDisabled = input(false);
  readonly retireDisabled = input(false);
  readonly enableDisabled = input(false);
  /** Why the last retire or enable was refused, `null` while none was. */
  readonly verbError = input<string | null>(null);

  readonly edit = output<void>();
  readonly retire = output<void>();
  readonly enable = output<void>();

  protected readonly facts = computed((): readonly KitFact[] =>
    this.record().settings.map((s) => ({ label: s.key, value: s.value, testid: `${this.prefix()}-setting-${s.key}` })),
  );
}

import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import type { ConfigFieldChange } from './config-history.domain';

import { fieldDiffRows } from './config-field-diff.model';

/**
 * A field diff — one `field  old → new` row per generated `ConfigFieldChange`.
 * Presentational.
 */
@Component({
  selector: 'app-config-field-diff',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './config-field-diff.html',
  styleUrl: './config-field-diff.css',
})
export class ConfigFieldDiff {
  readonly diff = input.required<readonly ConfigFieldChange[]>();
  readonly testid = input<string | null>(null);

  protected readonly rows = computed(() => fieldDiffRows(this.diff()));
}

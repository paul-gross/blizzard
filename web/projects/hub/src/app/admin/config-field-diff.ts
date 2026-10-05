import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
import type { FieldChangeView } from 'fleet';

import { fieldDiffRows } from './config-field-diff.model';

/**
 * A field diff — one `field  old → new` row per generated `FieldChangeView`. The one
 * rendering of a config change's fields, shared by a change log entry and an edit's
 * "Will change" preview. Presentational.
 */
@Component({
  selector: 'app-config-field-diff',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './config-field-diff.html',
  styleUrl: './config-field-diff.css',
})
export class ConfigFieldDiff {
  readonly diff = input.required<readonly FieldChangeView[]>();
  readonly testid = input<string | null>(null);

  protected readonly rows = computed(() => fieldDiffRows(this.diff()));
}

import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import type { QuestionView } from '../api/hub';
import { compactRef } from '../compact-ref';
import { KitAsyncState, type KitAsyncStateValue } from '../kit/kit-async-state';
import { KitPanel } from '../kit/kit-panel';

/**
 * The open-questions rail's presentational half (issue #80) — the ask list
 * and the click-to-open row. Renders exactly the questions it is handed;
 * injects no query.
 *
 * Its test handles are `rail-`prefixed because the chunk detail dock renders the
 * *same* chunk's ask at the same time, under `open-question` / `question-text`. Two
 * components sharing a handle makes a browser test's locator ambiguous — it matches
 * both and fails strict-mode — so the rail names its own.
 */
@Component({
  selector: 'fleet-questions-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitPanel],
  templateUrl: './questions-view.html',
  styleUrl: './questions-view.css',
})
export class QuestionsPanelView {
  /** Every open ask across the fleet. */
  readonly questions = input.required<readonly QuestionView[]>();

  /** The questions query's async state (AC 3). */
  readonly state = input.required<KitAsyncStateValue>();

  /** Emitted with a chunk id when an ask is activated — opens it in the detail panel. */
  readonly selectChunk = output<string>();

  protected shortId(chunkId: string): string {
    return compactRef(chunkId);
  }
}

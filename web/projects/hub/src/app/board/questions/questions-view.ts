import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';

import { type QuestionView, compactRef, runnerBoardParts, runnerTitle, type RunnerBoardParts, KitAsyncState, type KitAsyncStateValue, KitPanel, KitSelectRow } from 'fleet';

/**
 * The open-questions rail's presentational half — the ask list
 * and the click-to-open row. Renders exactly the questions it is handed;
 * injects no query.
 *
 * Its test handles are `rail-`prefixed because the chunk detail dock renders the
 * *same* chunk's ask at the same time, under `open-question` / `question-text`. Two
 * components sharing a handle makes a browser test's locator ambiguous — it matches
 * both and fails strict-mode — so the rail names its own.
 */
@Component({
  selector: 'app-questions-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitAsyncState, KitPanel, KitSelectRow],
  templateUrl: './questions-view.html',
  styleUrl: './questions-view.css',
})
export class QuestionsPanelView {
  /** Each runner's host name by runner id — a runner the map omits shows no host. */
  readonly runnerHosts = input<ReadonlyMap<string, string>>(new Map());

  /** Every open ask across the fleet. */
  readonly questions = input.required<readonly QuestionView[]>();

  /** The questions query's async state (AC 3). */
  readonly state = input.required<KitAsyncStateValue>();

  /** Emitted with a chunk id when an ask is activated — opens it in the detail panel. */
  readonly selectChunk = output<string>();

  protected shortId(chunkId: string): string {
    return compactRef(chunkId);
  }

  /** The asking runner's host and name, held apart so each clips on its own, as the board's do. */
  protected runner(question: QuestionView): RunnerBoardParts {
    return runnerBoardParts(question.runner_id, question.runner_name, this.runnerHosts().get(question.runner_id));
  }

  /** The asking runner's tooltip — its full display name, then its id. */
  protected runnerTooltip(question: QuestionView): string {
    return runnerTitle(question.runner_id, question.runner_name, this.runnerHosts().get(question.runner_id));
  }
}

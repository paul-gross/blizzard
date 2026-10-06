import { ChangeDetectionStrategy, Component, computed, output } from '@angular/core';

import { type KitAsyncStateValue, asyncState } from 'fleet';
import { QuestionsPanelView } from './questions-view';
import { injectHubQuestionsQuery } from './questions.query';

/**
 * The open-questions panel — every agent ask across the
 * fleet, in the right rail. A parked chunk's question is the one thing on this
 * board that blocks a worker on a human, so it is surfaced fleet-wide rather than
 * only inside the chunk nobody has selected yet; clicking an ask opens its chunk,
 * where the answer is given.
 *
 * A container: it owns the fleet-wide questions query through the
 * generated hub client (bzh:generated-client), and renders the presentational
 * {@link QuestionsPanelView}. The live-update service re-reads it on
 * `question-asked` / `question-answered`.
 */
@Component({
  selector: 'app-questions-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [QuestionsPanelView],
  templateUrl: './questions-panel.html',
})
export class QuestionsPanel {
  private readonly query = injectHubQuestionsQuery();

  /** Emitted with a chunk id when an ask is activated — opens it in the detail panel. */
  readonly selectChunk = output<string>();

  /** Every open ask across the fleet; empty until the first read resolves. */
  protected readonly questions = computed(() => this.query.data() ?? []);

  /** The questions query's async state. */
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.query, this.questions().length === 0));
}

import { ChangeDetectionStrategy, Component, computed, output } from '@angular/core';

import { asyncState, injectRunnerHostNames } from 'fleet/core';
import { type KitAsyncStateValue } from 'fleet/kit';
import { QuestionsPanelView } from './questions-view';
import { injectBoardQuestionsQuery } from './questions.query';

/**
 * The open-questions panel — every agent ask across the
 * fleet, in the right rail. A parked chunk's question is the one thing on this
 * board that blocks a worker on a human, so it is surfaced fleet-wide rather than
 * only inside the chunk nobody has selected yet; clicking an ask opens its chunk,
 * where the answer is given.
 *
 * A container: it owns the fleet-wide questions query through the
 * generated hub client (bzh:generated-client), and renders the presentational
 * {@link QuestionsPanelView}. Freshness: `EVENT_INVALIDATION_REGISTRY` (`web/projects/hub/src/app/core/live/hub-live-updates.ts`).
 */
@Component({
  selector: 'app-questions-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [QuestionsPanelView],
  templateUrl: './questions-panel.html',
  host: { '[style.display]': "state() === 'empty' ? 'none' : null" },
})
export class QuestionsPanel {
  private readonly query = injectBoardQuestionsQuery();
  protected readonly runnerHosts = injectRunnerHostNames();

  /** Emitted with a chunk id when an ask is activated — opens it in the detail panel. */
  readonly selectChunk = output<string>();

  /** Every open ask across the fleet; empty until the first read resolves. */
  protected readonly questions = computed(() => this.query.data() ?? []);

  /** The questions query's async state. */
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.query, this.questions().length === 0));
}

import { ChangeDetectionStrategy, Component, computed, output } from '@angular/core';

import { QuestionsPanelView } from './questions-view';
import { injectHubQuestionsQuery } from './questions.query';

/**
 * The open-questions panel (MVP criterion 7) — every agent ask across the
 * fleet, in the right rail. A parked chunk's question is the one thing on this
 * board that blocks a worker on a human, so it is surfaced fleet-wide rather than
 * only inside the chunk nobody has selected yet; clicking an ask opens its chunk,
 * where the answer is given.
 *
 * A container (issue #80): it owns the fleet-wide questions query through the
 * generated hub client (bzh:generated-client), and renders the presentational
 * {@link QuestionsPanelView}. The live-update service re-reads it on
 * `question-asked` / `question-answered`.
 */
@Component({
  selector: 'fleet-questions-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [QuestionsPanelView],
  template: `<fleet-questions-view [questions]="questions()" (selectChunk)="selectChunk.emit($event)" />`,
})
export class QuestionsPanel {
  private readonly query = injectHubQuestionsQuery();

  /** Emitted with a chunk id when an ask is activated — opens it in the detail panel. */
  readonly selectChunk = output<string>();

  /** Every open ask across the fleet; empty until the first read resolves. */
  protected readonly questions = computed(() => this.query.data() ?? []);
}

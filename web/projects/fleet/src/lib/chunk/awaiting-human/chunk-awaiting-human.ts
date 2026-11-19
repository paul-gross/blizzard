import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitSectionHead } from '../../kit/kit-section-head';
import { FleetWhen } from '../../core/when-display/fleet-when';
import { injectRunnerHostNames, runnerDisplayName } from '../../core/runner-display-name';
import { type AwaitingHuman, type AwaitingHumanFormActions, type AwaitingQuestion, deliveryLine } from './awaiting-human.domain';
import { ChunkDecisionForm } from './chunk-decision-form';
import { ChunkQuestionForm } from './chunk-question-form';

/**
 * The chunk's awaiting-human gate — whatever the chunk waits on
 * a human for: an open **question** with its answer form (MVP
 * criterion 7), an open gate **decision** with its note form and choices (MVP
 * criterion 12). The escalation box is its composition sites' to render.
 *
 * Below those, the **answered trail**: a recently answered question stays
 * rendered with who answered it, what they said, and whether the runner has delivered
 * the answer into the resumed session — the return leg of the rendezvous, so the person
 * who answered sees it arrive instead of watching the row disappear.
 *
 * Reads the {@link AwaitingHuman} state. Each writable question and gate gets its own
 * form ({@link ChunkQuestionForm}, {@link ChunkDecisionForm}), saving through `actions`.
 * Read-only is structural: with no `actions`, or without the permission, no form is
 * created and only the question text, the trail, and the gate metadata render.
 */
@Component({
  selector: 'fleet-chunk-detail-awaiting-human',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkDecisionForm, ChunkQuestionForm, FleetWhen, KitSectionHead],
  templateUrl: './chunk-awaiting-human.html',
  styleUrl: './chunk-awaiting-human.css',
})
export class ChunkAwaitingHuman {
  /** The one independently placed part to render. */
  readonly section = input.required<'asks' | 'answered' | 'decisions'>();

  /** What the chunk waits on a human for. */
  readonly state = input.required<AwaitingHuman>();

  /** Whether the current identity may answer an open question (`question:answer`).
   * Withholds the answer form when `false`, though the question
   * text itself still shows — a `guest` reads that a chunk is waiting, just cannot
   * act on it. `null`/pending resolves to `false` (hidden until confirmed). */
  readonly canAnswer = input(false);

  /** Whether the current identity may resolve an open gate decision (`gate:resolve`).
   * Withholds the note form and choices when `false`; `null`/pending resolves to
   * `false`. */
  readonly canResolve = input(false);

  /** How the app saves an answer or a resolution; `null` renders read-only. */
  readonly actions = input<AwaitingHumanFormActions | null>(null);

  private readonly runnerHosts = injectRunnerHostNames();

  /** A gate-imposing runner's display name, with its host when known. */
  protected hostedRunnerName(runnerId: string, name: string | null | undefined): string {
    return runnerDisplayName(runnerId, name, this.runnerHosts().get(runnerId));
  }

  /** The delivery leg of one answered question's trail. */
  protected deliveryLine(question: AwaitingQuestion): string {
    return deliveryLine(question, this.state().terminal);
  }
}

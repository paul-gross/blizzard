import { ChangeDetectionStrategy, Component, input, output, signal } from '@angular/core';

import { KitButton } from '../../kit/kit-button';
import { KitChip } from '../../kit/kit-chips';
import { KitToolbar, KitToolbarWidget } from '../../kit/kit-toolbar';
import { KitSectionHead } from '../../kit/kit-section-head';
import { KitTextInput } from '../../kit/kit-text-input';
import { FleetWhen } from '../../core/when-display/fleet-when';
import { injectRunnerHostNames, runnerDisplayName } from '../../core/runner-display-name';
import { type AwaitingHuman, type AwaitingQuestion, deliveryLine } from './awaiting-human.domain';

/** Emitted when the operator answers a chunk's open question from the dock. */
export interface AnswerQuestionEvent {
  readonly questionId: string;
  readonly answer: string;
  readonly chunkId: string;
}

/** Emitted when the operator resolves a chunk's open gate decision from the dock. */
export interface ResolveDecisionEvent {
  readonly decisionId: string;
  readonly choice: string;
  readonly chunkId: string;
  /** The operator's note, binding every later session on the chunk — absent when left blank. */
  readonly note?: string;
}

/**
 * The chunk's awaiting-human gate — whatever the chunk waits on
 * a human for: an open **question** with an inline **Answer** action (MVP
 * criterion 7), an open gate **decision** as **choice buttons** (MVP
 * criterion 12). The escalation box is its composition sites' to render.
 *
 * Below those, the **answered trail**: a recently answered question stays
 * rendered with who answered it, what they said, and whether the runner has delivered
 * the answer into the resumed session — the return leg of the rendezvous, so the person
 * who answered sees it arrive instead of watching the row disappear.
 *
 * Presentational only: it reads the {@link AwaitingHuman} state and emits `answerQuestion`
 * / `resolveDecision`; the mutations those events drive live in the
 * container.
 */
@Component({
  selector: 'fleet-chunk-detail-awaiting-human',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FleetWhen, KitButton, KitChip, KitToolbar, KitToolbarWidget, KitSectionHead, KitTextInput],
  templateUrl: './chunk-awaiting-human.html',
  styleUrl: './chunk-awaiting-human.css',
})
export class ChunkAwaitingHuman {
  /** Render one independently placed part, or the complete legacy presentation. */
  readonly section = input<'all' | 'asks' | 'answered' | 'decisions'>('all');

  /** What the chunk waits on a human for. */
  readonly state = input.required<AwaitingHuman>();

  /** Whether the current identity may answer an open question (`question:answer`).
   * Withholds the answer input/chips when `false`, though the question
   * text itself still shows — a `guest` reads that a chunk is waiting, just cannot
   * act on it. `null`/pending resolves to `false` (hidden until confirmed). */
  readonly canAnswer = input(false);

  /** Whether the current identity may resolve an open gate decision (`gate:resolve`).
   * Withholds the choice chips when `false`; `null`/pending resolves to
   * `false`. */
  readonly canResolve = input(false);

  /** Whether the resolve-decision mutation is in flight — disables the choice chips so
   * a double click cannot resolve the gate twice. */
  readonly resolvePending = input(false);

  /** The ids of the questions an answer mutation is in flight for — disables just those
   * questions' option chips and Answer button, so a double click cannot submit the same
   * question twice while the chunk's other questions stay answerable. */
  readonly pendingAnswerQuestionIds = input<readonly string[]>([]);

  /** Emitted when the operator answers an open question (MVP criterion 7). */
  readonly answerQuestion = output<AnswerQuestionEvent>();

  /** Emitted when the operator resolves an open gate decision. */
  readonly resolveDecision = output<ResolveDecisionEvent>();

  /** Each open question's in-progress answer, keyed by `question_id`. */
  private readonly answerDrafts = signal<Readonly<Partial<Record<string, string>>>>({});

  /** The open gate decision's in-progress note, sent with whichever choice is picked. */
  protected readonly noteDraft = signal('');

  private readonly runnerHosts = injectRunnerHostNames();

  /** A gate-imposing runner's display name, with its host when known. */
  protected hostedRunnerName(runnerId: string, name: string | null | undefined): string {
    return runnerDisplayName(runnerId, name, this.runnerHosts().get(runnerId));
  }

  /** The delivery leg of one answered question's trail. */
  protected deliveryLine(question: AwaitingQuestion): string {
    return deliveryLine(question, this.state().terminal);
  }

  /** The live draft for one question's answer field — `''` for a question the
   * operator has not typed into yet. */
  protected answerDraft(questionId: string): string {
    return this.answerDrafts()[questionId] ?? '';
  }

  /** Records a question's answer field as the operator types — carried up from
   * `KitTextInput`'s `valueChange`. */
  protected onAnswerInput(questionId: string, value: string): void {
    this.answerDrafts.update((drafts) => ({ ...drafts, [questionId]: value }));
  }

  /** Emit an answer for a question — no-op on an empty answer — and clear its draft. */
  protected submitAnswer(questionId: string, answer: string): void {
    const trimmed = answer.trim();
    if (!trimmed) return;
    this.answerQuestion.emit({ questionId, answer: trimmed, chunkId: this.state().chunk_id });
    this.answerDrafts.update((drafts) => {
      const next = { ...drafts };
      delete next[questionId];
      return next;
    });
  }

  /** Emit a resolution for the open gate decision, with the note when one was typed, and
   * clear the note draft. */
  protected resolve(decisionId: string, choice: string): void {
    const note = this.noteDraft().trim();
    this.resolveDecision.emit({
      decisionId,
      choice,
      chunkId: this.state().chunk_id,
      ...(note ? { note } : {}),
    });
    this.noteDraft.set('');
  }
}

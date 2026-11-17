import { ChangeDetectionStrategy, Component, input } from '@angular/core';

import { KitMarkdown } from './kit-markdown';

/**
 * A block of prose a coding agent reads or writes — a graph node's prompt, a
 * sweep measurement, a proposal's rationale — set apart from the surrounding UI
 * chrome by the transcript's own gutter: a hairline rail spanning the prose's
 * full height, ticked once where it begins, with the label and body beside it.
 * Lifted from `transcripts/transcript-viewer.css`'s `.turn` shape into the kit,
 * so an agent's words look the same here as they do in a transcript.
 *
 * `text` is an input, not `<ng-content>`, deliberately: in `plain` format the
 * body renders `white-space: pre-wrap` so the prose's own line breaks are
 * preserved, and projected content would carry the consumer template's own
 * indentation into that output. In `markdown` format the body is a
 * `fleet-kit-markdown` in place of that plain element — never nested in it — so
 * it inherits neither `pre-wrap` nor the monospace face; the rail and label stay
 * the same in both.
 */
@Component({
  selector: 'fleet-kit-prose-block',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitMarkdown],
  templateUrl: './kit-prose-block.html',
  styleUrl: './kit-prose-block.css',
})
export class KitProseBlock {
  /**
   * Which side of the agent this prose sits on, carrying the transcript's own
   * color meaning: `output` is something an agent wrote and a person is reading
   * (a proposal's case, a finding's summary, a sweep measurement), `context` is
   * something a person wrote and an agent will read (a graph node's prompt).
   * Defaults to `output` — the commoner case by far, and the safer one to get
   * wrong, since mislabelling agent output as context implies the platform feeds
   * its own findings back in as instructions.
   */
  readonly kind = input<'output' | 'context'>('output');

  /** A short heading above the prose — the step's name, the scope's slug. Omitted when null. */
  readonly label = input<string | null>(null);
  readonly text = input.required<string>();

  /**
   * How `text` is read: `plain` keeps the prose verbatim, its own line breaks
   * and indentation intact, in the monospace face; `markdown` renders it through
   * `fleet-kit-markdown` for prose authored as Markdown (a finding's summary).
   */
  readonly format = input<'plain' | 'markdown'>('plain');
  readonly testid = input<string | null>(null);
}

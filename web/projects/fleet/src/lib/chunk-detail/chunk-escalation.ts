import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { EscalationCause, type ChunkDetail, type ChunkEscalationView } from '../api/hub';
import { createCopyFlash } from '../clipboard';
import { KitButton } from '../kit/kit-button';

/**
 * The chunk's open escalation, needing a human takeover. Presentational only: it
 * holds the `detail` input and derives everything else
 * (`bzh:frontend-container-presentational`) — no query, no store access.
 *
 * `wrapped_takeover_command` is the `blizzard runner takeover` form the runner
 * composes; it is primary whenever present, with the raw `takeover_command`
 * demoted to a collapsed fallback disclosure below it. When wrapped is absent but
 * raw is present, the raw field renders as the primary copyable command instead —
 * under framing that does not tell the operator to run it, because the wire carries
 * no discriminator between a runner-composed resume command and the hub-authored
 * guidance prose that occupies the same field.
 * Wrapped-vs-raw rules and the wire field's own optionality:
 * `blizzard-context:/domain/humans/escalation.md` §The commands an escalation carries,
 * and the generated `ChunkEscalationView`.
 */
@Component({
  selector: 'fleet-chunk-detail-escalation',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitButton],
  templateUrl: './chunk-escalation.html',
  styleUrl: './chunk-escalation.css',
})
export class ChunkEscalation {
  /** The chunk aggregate to render the open escalation of, if any. */
  readonly detail = input.required<ChunkDetail>();

  /** The takeover-command copy button's clipboard action and transient "Copied" state. */
  private readonly copyFlash = createCopyFlash();
  protected readonly copied = this.copyFlash.copied;

  /** The chunk's open escalation, if it currently needs a human takeover. */
  protected readonly escalation = computed<ChunkEscalationView | null>(() => this.detail().escalation ?? null);

  /** The opening sentence, chosen by cause: a recognized cause reads as prose, an
   * unrecognized one renders its raw value, and an absent one names no actor. */
  protected readonly causeSentence = computed<string>(() => {
    const esc = this.escalation();
    if (!esc) return '';
    const cause = esc.cause;
    if (!cause) return `Escalated (epoch ${esc.epoch}).`;
    const sentence = isEscalationCause(cause) ? CAUSE_SENTENCES[cause] : `Escalated: ${cause}`;
    return `${sentence} (epoch ${esc.epoch}).`;
  });

  /** Whether the escalation carries a runner-composed wrapped command — the
   * primary form once present. */
  protected readonly hasWrapped = computed<boolean>(() => !!this.escalation()?.wrapped_takeover_command);

  /** Whether the escalation carries the raw field — evaluated once `hasWrapped`
   * above is false, distinguishing a raw-only escalation (render it as the primary
   * copyable command) from a genuinely empty one (render neither). See the class doc
   * for what each shape means. */
  protected readonly hasCommand = computed<boolean>(() => !!this.escalation()?.takeover_command);

  /** The command the copy button and primary `<code>` carry: the wrapped
   * `blizzard runner takeover` form when present, else the raw field — the fallback
   * the wire contract promises. Both operands are reachable: the wrapped branch and
   * the raw-only branch each render this. */
  protected readonly primaryCommand = computed<string>(() => {
    const esc = this.escalation();
    if (!esc) return '';
    return esc.wrapped_takeover_command || esc.takeover_command;
  });

  /** Copy the primary takeover command to the clipboard, flashing "Copied" when it lands. */
  protected copyTakeover(command: string): void {
    this.copyFlash.copy(command);
  }
}

/** The sentence each recognized escalation cause opens with, ahead of ` (epoch N).`.
 * The hub-authored causes (`bounce-cap`, `migration-target-unresolvable`) name no
 * worker — no worker was involved. */
const CAUSE_SENTENCES: Readonly<Record<EscalationCause, string>> = {
  [EscalationCause.RETRIES_EXHAUSTED]: 'The worker exhausted its retries',
  [EscalationCause.OWNER_UNRESOLVABLE]: "The runner could not resolve the chunk's owner",
  [EscalationCause.NO_ACCEPTABLE_HARNESS]: 'No acceptable harness was available for the chunk',
  [EscalationCause.SPEND_CAP]: 'The worker hit its spend cap',
  [EscalationCause.BOUNCE_CAP]: 'The chunk reached its bounce cap',
  [EscalationCause.MIGRATION_TARGET_UNRESOLVABLE]: "The chunk's migration target could not be resolved",
};

/** The wire carries an escalation's cause as an open string — a stored cause is free
 * text on ingest — so it narrows here, and an unrecognized value renders raw. */
function isEscalationCause(value: string): value is EscalationCause {
  return (Object.values(EscalationCause) as readonly string[]).includes(value);
}

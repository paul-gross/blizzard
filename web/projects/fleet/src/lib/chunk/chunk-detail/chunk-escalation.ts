import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';

import { openLookup } from '../../core/open-lookup';
import { KitSectionHead } from '../../kit/kit-section-head';
import { EscalationTakeover } from './escalation-takeover';
import type { ChunkEscalationState, EscalationDetail } from './escalation.model';

/**
 * The chunk's open escalation, needing a human takeover. Presentational only: it
 * holds the `detail` input and derives everything else
 * (`bzh:frontend-container-presentational`) — no query, no store access.
 *
 * The takeover commands render through `fleet-chunk-detail-escalation-takeover`, which owns the wrapped-vs-raw rule.
 */
@Component({
  selector: 'fleet-chunk-detail-escalation',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [KitSectionHead, EscalationTakeover],
  templateUrl: './chunk-escalation.html',
  styleUrl: './chunk-escalation.css',
})
export class ChunkEscalation {
  /** The chunk aggregate to render the open escalation of, if any. */
  readonly detail = input.required<EscalationDetail>();

  /** The chunk's open escalation, if it currently needs a human takeover. */
  protected readonly escalation = computed<ChunkEscalationState | null>(() => this.detail().escalation);

  /** The opening sentence, chosen by cause: a recognized cause reads as prose, an
   * unrecognized one renders its raw value, and an absent one names no actor. */
  protected readonly causeSentence = computed<string>(() => {
    const esc = this.escalation();
    if (!esc) return '';
    const cause = esc.cause;
    if (!cause) return `Escalated (epoch ${esc.epoch}).`;
    const sentence = CAUSE_SENTENCES(cause);
    return `${sentence} (epoch ${esc.epoch}).`;
  });
}

/** The sentence each recognized escalation cause opens with, ahead of ` (epoch N).`.
 * The hub-authored causes (`bounce-cap`, `migration-target-unresolvable`, `escalate-choice`)
 * name no worker — none need have been involved. The wire carries a cause as an open string — a stored
 * cause is free text on ingest — so an unrecognized value renders raw. */
const CAUSE_SENTENCES = openLookup<string, string>(
  {
    'retries-exhausted': 'The worker exhausted its retries',
    'owner-unresolvable': "The runner could not resolve the chunk's owner",
    'no-acceptable-harness': 'No acceptable harness was available for the chunk',
    'harness-version-changed': "The runner refused to launch the worker on its harness's changed version",
    'spend-cap': 'The worker hit its spend cap',
    'bounce-cap': 'The chunk reached its bounce cap',
    'migration-target-unresolvable': "The chunk's migration target could not be resolved",
    'hub-action-missing': 'The hub node the chunk stands at names no action the hub can take',
    'hub-action-stuck': "The hub node's action was cut off at its deadline three turns in a row",
    'unroutable-outcome': "The hub node's answer has no edge to route the chunk on",
    'forge-auth': "The repository's forge refused the hub's credential",
    'forge-config': "The repository's forge could not find the repository with the hub's credential",
    'forge-capability-missing': "The repository's forge does not offer what the hub node's action needs",
    'change-closed': 'The change was closed without merging',
    'branch-protected': 'The forge refused to update a protected branch',
    'merge-method-refused': 'No allowed merge method could be used',
    'missing-checks': 'No CI checks were observed for the revision',
    'park-timeout': 'The wait for the change to merge timed out',
    'escalate-choice': 'A choice the chunk took escalated it to a person',
  },
  (raw) => `Escalated: ${raw}`,
);

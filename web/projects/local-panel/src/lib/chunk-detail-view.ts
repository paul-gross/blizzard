import { ChangeDetectionStrategy, Component, TemplateRef, input } from '@angular/core';
import { KitAsyncState, type KitFact, KitFactList, type runnerApi } from 'fleet';

import { HeartbeatFreshness } from './heartbeat-freshness';

/**
 * {@link MachineDetail}'s presentational sibling (`bzh:frontend-container-presentational`):
 * plain inputs only, injects nothing, and owns the execution-facts template — the
 * container keeps the `fleet-kit-panel` shell and header projection (which must stay
 * in the template that mounts the panel), the severable `ChunkDetail` read, and the
 * ticking clock {@link heartbeatLabel} is derived from.
 */
@Component({
  selector: 'local-machine-detail-view',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HeartbeatFreshness, KitAsyncState, KitFactList],
  templateUrl: './chunk-detail-view.html',
  styleUrl: './chunk-detail-view.css',
})
export class MachineDetailView {
  /** The chunk's newest attempt, or `null` when nothing is selected — the rest
   * state renders in its place. */
  readonly lease = input<runnerApi.LeaseView | null>(null);

  /** The open escalation for this chunk, when there is one — carries the resume command. */
  readonly escalation = input<runnerApi.EscalationView | null>(null);

  /** {@link lease}'s compact ref, resolved by the container. */
  readonly leaseRef = input('');

  /** `-34s` shorthand, or `—` before the first beat / past the skew bound —
   * the container's own ticking clock. */
  readonly heartbeatLabel = input('—');

  /** The execution-facts table's rows — a method, not a stored computed, since the
   * lease/workdir/heartbeat rows need the `<ng-template>`s this template declares for them
   * (`KitFactList`'s own templated-row contract). */
  protected factRows(
    l: runnerApi.LeaseView,
    leaseValue: TemplateRef<unknown>,
    workdirValue: TemplateRef<unknown>,
    heartbeatValue: TemplateRef<unknown>,
  ): readonly KitFact[] {
    return [
      { label: 'lease', template: leaseValue },
      { label: 'session', value: l.session_id ?? '—' },
      { label: 'pid', value: l.pid !== null && l.pid !== undefined ? String(l.pid) : '—' },
      { label: 'env', value: l.environment_id ?? 'released' },
      { label: 'workdir', template: workdirValue },
      { label: 'heartbeat', template: heartbeatValue },
    ];
  }
}

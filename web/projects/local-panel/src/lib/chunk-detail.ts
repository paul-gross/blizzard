import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { ageMs, compactRef, formatAge, KitChips, type KitChipOption, type runnerApi } from 'fleet';

import type { MachineChunkStatus } from './chunk-status';
import { HeartbeatFreshness } from './heartbeat-freshness';
import { TranscriptPanel } from './transcript-panel';

/**
 * The machine detail dock — the discovery mock's "machine detail" panel for the
 * selected chunk: execution facts *from this box only* (lease, session, pid,
 * env, workdir, heartbeat), the escalation resume command when one is open,
 * and the transcript inline at the bottom (there is no cross-view navigation
 * yet, so the transcript list lives here rather than behind a link).
 *
 * The summary facts, status, and escalation all render off the chunk's newest
 * lease (the last entry of the `leases` list the shell hands in, oldest →
 * newest) — this dock owns no list read of its own. A chunk is often processed
 * across several attempts, each its own lease with its own transcript, so when
 * there is more than one the dock renders a tab per attempt (issue #98): the
 * newest is selected by default, and picking a tab feeds that attempt's lease
 * id to {@link TranscriptPanel}'s existing `leaseId` input. The dock only
 * passes the id and never branches on the transcript's states.
 */
@Component({
  selector: 'local-machine-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HeartbeatFreshness, KitChips, TranscriptPanel],
  template: `
    <div class="detail" data-testid="machine-detail">
      @if (newestLease(); as l) {
        <header class="d-hdr">
          <span class="lbl">machine detail</span>
          <span class="cid" data-testid="detail-chunk-ref">{{ chunkRef() }}</span>
          <span class="spacer"></span>
          <span class="st" [attr.data-tone]="status()?.tone" data-testid="machine-detail-status">
            {{ status()?.label }} · node {{ l.node_name }} · a{{ l.epoch }}
          </span>
        </header>
        <div class="facts">
          <dl class="kv" data-testid="detail-facts">
            <dt>lease</dt>
            <dd>
              {{ leaseRef() }} · epoch {{ l.epoch }} <small class="full">{{ l.lease_id }}</small>
            </dd>
            <dt>session</dt>
            <dd>{{ l.session_id ?? '—' }}</dd>
            <dt>pid</dt>
            <dd>{{ l.pid ?? '—' }}</dd>
            <dt>env</dt>
            <dd>{{ l.environment_id ?? 'released' }}</dd>
            <dt>workdir</dt>
            <dd class="path">{{ l.workdir ?? '—' }}</dd>
            <dt>heartbeat</dt>
            <dd>
              <span class="hb-line">
                <span>{{ heartbeatLabel() }}</span>
                <local-heartbeat-freshness
                  class="hb-bar"
                  [lastHeartbeatAt]="l.last_heartbeat_at"
                  [stale]="l.state === 'stale'"
                />
              </span>
            </dd>
          </dl>
          @if (escalation(); as esc) {
            <div class="resume-box" data-testid="detail-resume">
              <span class="lbl">escalated — resume session</span>
              <code>{{ esc.resume_command || '(no session to resume)' }}</code>
            </div>
          }
        </div>
        @if (attemptOptions().length > 1) {
          <div class="attempts" data-testid="attempt-tabs">
            <fleet-kit-chips
              [options]="attemptOptions()"
              [selectedValue]="activeAttemptLeaseId()"
              (choose)="selectAttempt.emit($event)"
            />
          </div>
        }
        <div class="transcript" data-testid="detail-transcript">
          <local-transcript-panel [leaseId]="activeAttemptLeaseId()" />
        </div>
      } @else {
        <p class="status" data-testid="detail-empty">SELECT A CHUNK</p>
      }
    </div>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
      font-family: var(--mono);
      font-variant-numeric: tabular-nums;
    }
    .detail {
      display: flex;
      flex-direction: column;
      height: 100%;
      position: relative;
    }
    .lbl {
      font-size: var(--fs-label);
      letter-spacing: 0.18em;
      text-transform: uppercase;
      color: var(--label);
    }
    .d-hdr {
      flex: none;
      display: flex;
      align-items: baseline;
      gap: 10px;
      padding: 6px 8px;
      border-bottom: 1px solid var(--bezel);
      background: linear-gradient(180deg, var(--header-hi), var(--header-lo));
    }
    .cid {
      color: var(--amber-hi);
      font-size: var(--fs-md);
    }
    .spacer {
      flex: 1;
    }
    .st {
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      text-transform: uppercase;
      color: var(--label);
    }
    .st[data-tone='running'] {
      color: var(--amber);
    }
    .st[data-tone='stale'],
    .st[data-tone='needs'] {
      color: var(--red);
    }
    .st[data-tone='waiting'],
    .st[data-tone='takeover'] {
      color: var(--amber-hi);
    }
    .st[data-tone='spawning'] {
      color: var(--cyan);
    }
    .st[data-tone='done'] {
      color: var(--green);
    }
    .facts {
      flex: none;
      padding: 6px 8px;
      border-bottom: 1px solid var(--bezel);
    }
    .kv {
      display: grid;
      grid-template-columns: 92px 1fr;
      gap: 2px 10px;
      margin: 0;
      font-size: var(--fs-sm);
    }
    .kv dt {
      color: var(--label);
      text-transform: uppercase;
      font-size: var(--fs-label);
      letter-spacing: 0.14em;
      align-self: baseline;
    }
    .kv dd {
      margin: 0;
      color: var(--text);
      min-width: 0;
    }
    .kv dd .full {
      color: var(--label-dim);
      font-size: var(--fs-label);
      margin-left: 6px;
    }
    .kv dd.path {
      color: var(--cyan);
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
    .hb-line {
      display: flex;
      align-items: center;
      gap: 10px;
    }
    .hb-bar {
      flex: 0 0 180px;
    }
    .resume-box {
      margin-top: 8px;
      padding: 6px 8px;
      border: 1px solid var(--red-dim);
      background: color-mix(in srgb, var(--red-dim) 12%, transparent);
    }
    .resume-box .lbl {
      color: var(--red);
      display: block;
      margin-bottom: 4px;
    }
    .resume-box code {
      color: var(--text);
      font-size: var(--fs-sm);
      user-select: all;
    }
    .attempts {
      flex: none;
      padding: 6px 8px;
      border-bottom: 1px solid var(--bezel);
    }
    .transcript {
      flex: 1;
      min-height: 0;
      overflow-y: auto;
      position: relative;
    }
    .status {
      position: absolute;
      left: 50%;
      top: 50%;
      transform: translate(-50%, -50%);
      white-space: nowrap;
      color: var(--label-dim);
      font-size: var(--fs-sm);
      letter-spacing: 0.12em;
    }
  `,
})
export class MachineDetail {
  /** The selected chunk's attempts, oldest → newest; empty when nothing is
   * selected. The newest is the summary/status subject; each is a transcript tab. */
  readonly leases = input.required<readonly runnerApi.LeaseView[]>();

  /** The derived machine-side status for the selected chunk (shell-folded). */
  readonly status = input<MachineChunkStatus | null>(null);

  /** The open escalation for this chunk, when there is one — carries the resume command. */
  readonly escalation = input<runnerApi.EscalationView | null>(null);

  /** The chunk's newest attempt (the `leases` list's last entry) — the summary,
   * status, and escalation all render off it, whichever attempt tab is active. */
  protected readonly newestLease = computed<runnerApi.LeaseView | null>(() => this.leases().at(-1) ?? null);

  /**
   * The attempt whose transcript the dock shows — the container's effective pick,
   * URL-derived (issue #99). Presentational: the dock renders whichever tab this
   * names and emits {@link selectAttempt} on a pick; the container owns which
   * attempt applies (falling back to newest when a pick ages out or the chunk
   * changes) and writes it to the URL. Defaults to `null` before a chunk is
   * selected — the summary already falls back to the newest lease.
   */
  readonly activeAttemptLeaseId = input<string | null>(null);

  /** Emitted with an attempt's lease id when the operator picks its tab — the
   * container writes it to the URL as the new selection. */
  readonly selectAttempt = output<string>();

  /** One selectable chip per attempt (oldest → newest), keyed by lease id and
   * labelled with the attempt ordinal + its state, for the `KitChips` tab row. */
  protected readonly attemptOptions = computed<readonly KitChipOption[]>(() =>
    this.leases().map((att) => ({
      value: att.lease_id,
      label: `a${att.epoch} ${this.attemptState(att)}`,
      testid: 'attempt-tab',
    })),
  );

  /** An attempt tab's state hint: the closure reason for a closed attempt (why
   * that attempt ended), else the live lease state. */
  private attemptState(att: runnerApi.LeaseView): string {
    return att.state === 'closed' ? (att.closure_reason ?? 'closed') : att.state;
  }

  protected readonly chunkRef = computed(() => {
    const l = this.newestLease();
    return l ? compactRef(l.chunk_id) : '';
  });

  protected readonly leaseRef = computed(() => {
    const l = this.newestLease();
    return l ? compactRef(l.lease_id) : '';
  });

  /**
   * `-34s` shorthand, or `—` before the first beat / past the skew bound —
   * decoration only; the server-derived state carries liveness (`bzh:utc-instants`).
   */
  protected readonly heartbeatLabel = computed<string>(() => {
    const l = this.newestLease();
    if (!l || l.state === 'closed') return '—';
    const age = ageMs(l.last_heartbeat_at, Date.now());
    return age === null ? '—' : formatAge(age);
  });
}

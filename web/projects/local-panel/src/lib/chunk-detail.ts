import { ChangeDetectionStrategy, Component, computed, input, signal } from '@angular/core';
import { ageMs, compactRef, formatAge, type runnerApi } from 'fleet';

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
  imports: [HeartbeatFreshness, TranscriptPanel],
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
        @if (leases().length > 1) {
          <div class="attempts" role="tablist" data-testid="attempt-tabs">
            @for (att of leases(); track att.lease_id) {
              <button
                type="button"
                role="tab"
                class="tab"
                [class.active]="att.lease_id === activeAttemptLeaseId()"
                [attr.aria-selected]="att.lease_id === activeAttemptLeaseId()"
                [attr.data-lease-id]="att.lease_id"
                data-testid="attempt-tab"
                (click)="selectAttempt(att.lease_id)"
              >
                a{{ att.epoch }} <small>{{ attemptState(att) }}</small>
              </button>
            }
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
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
      padding: 6px 8px;
      border-bottom: 1px solid var(--bezel);
    }
    .tab {
      display: inline-flex;
      align-items: baseline;
      gap: 5px;
      padding: 2px 8px;
      font-family: inherit;
      font-size: var(--fs-label);
      letter-spacing: 0.1em;
      text-transform: uppercase;
      color: var(--label);
      background: var(--panel);
      border: 1px solid var(--bezel);
      cursor: pointer;
    }
    .tab:hover {
      color: var(--text);
      border-color: var(--line);
    }
    .tab.active {
      color: var(--amber-hi);
      border-color: var(--amber);
    }
    .tab small {
      color: var(--label-dim);
      font-size: var(--fs-label);
    }
    .tab.active small {
      color: var(--amber);
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
   * The operator's attempt pick, scoped to the chunk it was made on. Kept keyed
   * by `chunk_id` so it survives the leases list re-fetching (poll refresh keeps
   * the same attempt selected) but falls back to the newest when the chunk
   * changes or the picked attempt ages out of the recent-lease window.
   */
  private readonly picked = signal<{ chunkId: string; leaseId: string } | null>(null);

  /** The attempt whose transcript the dock shows — the operator's pick when it
   * still applies, else the newest attempt (the default). */
  protected readonly activeAttemptLeaseId = computed<string | null>(() => {
    const leases = this.leases();
    const newest = this.newestLease();
    const pick = this.picked();
    if (pick && pick.chunkId === newest?.chunk_id && leases.some((att) => att.lease_id === pick.leaseId)) {
      return pick.leaseId;
    }
    return newest?.lease_id ?? null;
  });

  protected selectAttempt(leaseId: string): void {
    const chunkId = this.newestLease()?.chunk_id;
    if (chunkId) this.picked.set({ chunkId, leaseId });
  }

  /** An attempt tab's state hint: the closure reason for a closed attempt (why
   * that attempt ended), else the live lease state. */
  protected attemptState(att: runnerApi.LeaseView): string {
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

import { ChangeDetectionStrategy, Component, computed, input } from '@angular/core';
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
 * Everything renders off the chunk's newest lease, handed in by the shell —
 * this dock owns no list read of its own. The transcript is
 * {@link TranscriptPanel}'s read, keyed by that lease id; the dock only passes
 * the id and never branches on the transcript's states.
 */
@Component({
  selector: 'fleet-chunk-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HeartbeatFreshness, TranscriptPanel],
  template: `
    <div class="detail" data-testid="machine-detail">
      @if (lease(); as l) {
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
                <fleet-heartbeat-freshness
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
        <div class="transcript" data-testid="detail-transcript">
          <fleet-transcript-panel [leaseId]="l.lease_id" />
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
export class ChunkDetail {
  /** The selected chunk's newest lease, or null when nothing is selected. */
  readonly lease = input.required<runnerApi.LeaseView | null>();

  /** The derived machine-side status for the selected chunk (shell-folded). */
  readonly status = input<MachineChunkStatus | null>(null);

  /** The open escalation for this chunk, when there is one — carries the resume command. */
  readonly escalation = input<runnerApi.EscalationView | null>(null);

  protected readonly chunkRef = computed(() => {
    const l = this.lease();
    return l ? compactRef(l.chunk_id) : '';
  });

  protected readonly leaseRef = computed(() => {
    const l = this.lease();
    return l ? compactRef(l.lease_id) : '';
  });

  /**
   * `-34s` shorthand, or `—` before the first beat / past the skew bound —
   * decoration only; the server-derived state carries liveness (`bzh:utc-instants`).
   */
  protected readonly heartbeatLabel = computed<string>(() => {
    const l = this.lease();
    if (!l || l.state === 'closed') return '—';
    const age = ageMs(l.last_heartbeat_at, Date.now());
    return age === null ? '—' : formatAge(age);
  });
}

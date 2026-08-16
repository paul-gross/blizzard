import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { ageMs, compactRef, formatAge, KitChips, KitPanel, type KitChipOption, type runnerApi } from 'fleet';

import { injectChunkDetailQuery } from './chunk-detail.query';
import { injectChunkPauseMutation } from './chunk-pause.mutations';
import type { MachineChunkStatus } from './chunk-status';
import { HeartbeatFreshness } from './heartbeat-freshness';
import { MachineDetailHeader } from './machine-detail-header';
import { TranscriptPanel } from './transcript-panel';

/** Statuses the hub's `PauseService` refuses to pause (`ChunkNotPausable`), mirrored
 * here so the dock never offers a Pause the server would answer with a 409 — the
 * same table `fleet/chunk-detail/chunk-detail-header.ts`'s own `NOT_PAUSABLE` pins
 * on the hub board's side of this same brake (issue #185). */
const NOT_PAUSABLE = new Set<runnerApi.ChunkStatus>(['done', 'stopped', 'delivering']);

/**
 * The machine detail dock's container (`bzh:frontend-container-presentational`) —
 * the discovery mock's "machine detail" panel for the selected chunk: execution
 * facts *from this box only* (lease, session, pid, env, workdir, heartbeat), the
 * escalation resume command when one is open, and the transcript inline at the
 * bottom (there is no cross-view navigation yet, so the transcript list lives
 * here rather than behind a link).
 *
 * The summary facts, status, and escalation all render off the chunk's newest
 * lease (the last entry of the `leases` list the shell hands in, oldest →
 * newest) — this dock owns no list read of its own. A chunk is often processed
 * across several attempts, each its own lease with its own transcript, so when
 * there is more than one the dock renders a tab per attempt (issue #98): the
 * newest is selected by default, and picking a tab feeds that attempt's lease
 * id to {@link TranscriptPanel}'s existing `leaseId` input. The dock only
 * passes the id and never branches on the transcript's states.
 *
 * The header ({@link MachineDetailHeader}, a presentational sibling) matches the
 * hub board's own chunk-detail header shape (issue #185, the model at
 * `fleet/chunk-detail/chunk-detail-header.ts`). Unlike the rest of this dock's
 * facts (container-folded, "one owner"), the work-item links and the pause fact
 * are this dock's own severable enrichment — the same self-fetching shape
 * `injectChunkTitleQuery` already established for the chunks list — read through
 * {@link injectChunkDetailQuery}, the runner's pass-through proxy onto the hub's
 * `ChunkDetail` aggregate, served whole as `ChunkDetailView`. `pause` is the *only*
 * way this panel learns a chunk is paused (it sits independently of the derived
 * {@link status}, which folds in machine-only facts the hub aggregate does not
 * carry), so Pause/Resume's own gating reads the fresh `pause`/`status` off that
 * read, never the machine-derived one.
 *
 * The dock paints its own panel chrome via {@link KitPanel} (issue #307) — the
 * same bezel/background every sibling region in `local-panel-layout.ts` wears —
 * rather than mounting bare. `KitPanel`'s `[header]` slot can only be filled
 * from the template that mounts the panel, so this container is the one place
 * that projection can happen; `MachineDetailHeader` is projected in with an
 * empty `label` on the panel itself, so exactly one header bar renders rather
 * than {@link MachineDetailHeader}'s own bar stacking below `KitPanel`'s. `.p-body`
 * scroll is disabled (`bodyScroll="false"`) because the content below manages
 * its own scrolling internally (the transcript's `.transcript` region) —
 * `KitPanel`'s own doc comment names this exact shape.
 */
@Component({
  selector: 'local-machine-detail',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [HeartbeatFreshness, KitChips, KitPanel, MachineDetailHeader, TranscriptPanel],
  template: `
    <fleet-kit-panel class="dock" data-testid="machine-detail" label="" [bodyScroll]="false">
      @if (newestLease(); as l) {
        <local-machine-detail-header
          header
          [chunkId]="l.chunk_id"
          [workRefs]="workRefs()"
          [statusLabel]="status()?.label ?? null"
          [statusTone]="status()?.tone"
          [nodeName]="l.node_name"
          [epoch]="l.epoch"
          [pause]="pause()"
          [pausable]="pausable()"
          (dismiss)="dismiss.emit()"
          (pauseChunk)="pauseMutation.mutate({ chunkId: $event, paused: true })"
          (resumeChunk)="pauseMutation.mutate({ chunkId: $event, paused: false })"
        />
      }
      <div class="detail">
        @if (newestLease(); as l) {
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
    </fleet-kit-panel>
  `,
  styles: `
    :host {
      display: block;
      height: 100%;
      font-family: var(--mono);
      font-variant-numeric: tabular-nums;
    }
    .dock {
      height: 100%;
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
    /* Shrinkable, not fixed — 180px is the desktop width, but the bar gives it
       up before the age label beside it is pushed out of a phone-width dock. */
    .hb-bar {
      flex: 0 1 180px;
      min-width: 0;
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

  /** Emitted when the operator dismisses the dock (issue #185) — the container
   * clears the selection, mirroring the hub header's own `dismiss`. */
  readonly dismiss = output<void>();

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

  /** The selected chunk's id — the severable detail read's subject. */
  protected readonly chunkId = computed<string | null>(() => this.newestLease()?.chunk_id ?? null);

  /**
   * The dock's own severable enrichment (issue #185) — the `ChunkDetailView` read,
   * not container-folded: work-item links and the pause fact reach the header through
   * this, the same self-fetching shape `injectChunkTitleQuery` established for the
   * chunks list (`chunk-title.query.ts`, `chunk-row.ts`).
   */
  protected readonly detailQuery = injectChunkDetailQuery(() => this.chunkId());

  /** The chunk's work refs, for the header — mirrors the hub header's own `pointers`. */
  protected readonly workRefs = computed<readonly runnerApi.WorkRefView[]>(
    () => this.detailQuery.data()?.work_refs ?? [],
  );

  /** The chunk's open operator pause, if any — read off the fresh
   * `ChunkDetailView.pause`, never the machine-derived {@link status}, which folds
   * in facts the hub aggregate does not carry (mirrors the hub header's own `pause`). */
  protected readonly pause = computed<runnerApi.PauseView | null>(() => this.detailQuery.data()?.pause ?? null);

  /** Whether an **unpaused** chunk may be paused — mirrors the hub `PauseService`'s
   * refusal so the header never offers a control the server would answer with a 409.
   * `undefined` (the read hasn't resolved yet) degrades to not-pausable, so no button
   * flashes before the fresh state is known. */
  protected readonly pausable = computed<boolean>(() => {
    const s = this.detailQuery.data()?.status;
    return s !== undefined && !NOT_PAUSABLE.has(s);
  });

  /** The header's Pause/Resume mutation — fired from its `pauseChunk`/`resumeChunk`
   * outputs, once the operator has already confirmed. */
  protected readonly pauseMutation = injectChunkPauseMutation();

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

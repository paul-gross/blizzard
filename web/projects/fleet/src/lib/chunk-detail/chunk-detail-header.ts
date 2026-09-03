import { ChangeDetectionStrategy, Component, computed, effect, input, output, signal } from '@angular/core';
import { RouterLink } from '@angular/router';

import type { ChunkDetail, ChunkStatus, PauseView, WorkRefView, RouteView } from '../api/hub';
import { ChunkBlocked } from '../chunk-blocked/chunk-blocked';
import { KitButton } from '../kit/kit-button';
import { KitTextInput } from '../kit/kit-text-input';

/** Statuses the hub's `PauseService` refuses to pause (`ChunkNotPausable`), mirrored
 * here so the dock never offers a Pause the server would answer with a 409 (issue #46).
 * A terminal or mid-delivery chunk has no work to stop.
 *
 * `paused` is deliberately **absent**: whether a chunk is already paused is not a
 * question `status` can answer (PAUSED derives below the human-gated states), so it is
 * never asked here — see {@link ChunkDetailHeader.pause}, which owns that half by
 * reading the fact. */
const NOT_PAUSABLE = new Set<ChunkStatus>(['done', 'stopped', 'delivering']);

/** Statuses the hub's `CompleteService` treats as a no-op rather than a transition
 * (issue #294): a `done` chunk is already done, so the dock withholds the control
 * rather than offer a click that writes nothing. Every other status is completable,
 * including `stopped` — unlike Pause/Detach, Complete does not hang off a live route,
 * and unlike Stop there is no un-complete verb, so this set has exactly one member. */
const NOT_COMPLETABLE = new Set<ChunkStatus>(['done']);

/** A declare or release, addressed by the ordered pair the hub itself takes (issue
 * #461) — the dock's only source for either, so both `declareDependency` and
 * `releaseDependency` share this one shape rather than two near-identical ones. */
export interface DependencyEvent {
  readonly chunkId: string;
  readonly prerequisiteChunkId: string;
}

/**
 * The chunk detail dock's header (issue #79) — the chunk's identity in the
 * board's own vocabulary (the short name, its work item, its state, and the
 * node it sits at), plus the operator actions that hang off it: the **route +
 * Detach** control (issue #42), **Pause/Resume** (issue #46), and dismiss.
 *
 * Detach is deliberately **not** requeue — it supersedes no escalation and
 * bumps no epoch, so a `needs_human` chunk detached this way still derives
 * `needs_human` afterward (`src/blizzard/hub/domain/detach.py`); this header
 * never claims otherwise. Pause/Resume switches on the pause **fact**
 * (`ChunkDetail.pause`), never on `status` — a chunk both paused and parked
 * on a question derives `waiting_on_human`, so a status-keyed switch would
 * never offer Resume. **Complete** (issue #294) is the operator's manual
 * counterpart to landing: reachable from any non-`done` status, including
 * `stopped` — unlike Stop, there is no un-complete verb, so the dock offers
 * no way back once clicked.
 *
 * Presentational only: it holds the detail input and emits `dismiss`,
 * `detach`, `pauseChunk`, `resumeChunk`, `complete`, `declareDependency`, and
 * `releaseDependency` (every write but `dismiss` guarded by a `confirm()` —
 * the one browser affordance this dock reaches for); the mutations those
 * events drive live in the container.
 */
@Component({
  selector: 'fleet-chunk-detail-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkBlocked, KitButton, KitTextInput, RouterLink],
  templateUrl: './chunk-detail-header.html',
  styleUrl: './chunk-detail-header.css',
})
export class ChunkDetailHeader {
  /** The chunk aggregate to render (identity, status, current node, pause, route). */
  readonly detail = input.required<ChunkDetail>();

  /** Whether the current identity may operate Pause/Resume/Detach (`chunk:control` —
   * issue #210). Withholds every one of those controls when `false` so a `guest`
   * never sees a write it cannot make; `null`/pending resolves to `false` (hidden
   * until confirmed), the same convention `RunnerPanel`'s `canPause` set. */
  readonly canControl = input(false);

  /** The chunk detail route's own path segments, before the chunk id — lets a
   * consumer outside the desktop board point the longname link elsewhere without
   * `fleet` hardcoding a hub route (`ChunkArtifacts`'s own `linkBase` follows the
   * same convention). */
  readonly linkBase = input<readonly string[]>(['/board', 'chunk']);

  /** Emitted when the operator dismisses the dock. */
  readonly dismiss = output<void>();

  /** Emitted with the chunk id when the operator confirms Detach (issue #42). */
  readonly detach = output<string>();

  /** Emitted with the chunk id when the operator confirms Pause (issue #46). */
  readonly pauseChunk = output<string>();

  /** Emitted with the chunk id when the operator confirms Resume (issue #46). */
  readonly resumeChunk = output<string>();

  /** Emitted with the chunk id when the operator confirms Complete (issue #294). */
  readonly complete = output<string>();

  /** Emitted with the prerequisite's chunk id when the blocked marking's dock-select
   * button is clicked (issue #461) — the same one-hop move a board card click already
   * makes, not a navigation. */
  readonly selectChunk = output<string>();

  /** Emitted when the operator confirms a dependency declaration on
   * {@link prerequisiteInput} (issue #461). */
  readonly declareDependency = output<DependencyEvent>();

  /** Emitted when the operator confirms releasing the standing dependency on
   * {@link prerequisiteInput} (issue #461). */
  readonly releaseDependency = output<DependencyEvent>();

  /** The chunk's work refs, for the header — each linked out to its source's web
   * address when the configured binding rendered one (a null `web_url` degrades to
   * plain text, no broken link). */
  protected readonly pointers = computed<readonly WorkRefView[]>(() => this.detail().work_refs ?? []);

  /** The chunk's open operator pause, if any — who set it (issue #46). Read off the
   * detail's `pause` fact, not `status`: a chunk both paused and parked on a question
   * derives `waiting_on_human`, so `status` alone would never surface it.
   *
   * This is also the **Pause/Resume switch**: non-null renders Resume, null renders
   * Pause (subject to {@link pausable}). `status` must never gate Resume. */
  protected readonly pause = computed<PauseView | null>(() => this.detail().pause ?? null);

  /** Whether an **unpaused** chunk may be paused — mirrors the hub `PauseService`'s
   * refusal (`ChunkNotPausable`) so the dock never offers a control the server would
   * answer with a 409 (issue #46), exactly as Detach shows only with a live route to
   * release (issue #42). `waiting_on_human`/`needs_human` are deliberately pausable. */
  protected readonly pausable = computed<boolean>(() => !NOT_PAUSABLE.has(this.detail().status));

  /** The chunk's live route, if any — Detach shows only while this is non-null
   * (issue #42): a chunk with no live route has nothing to release. */
  protected readonly route = computed<RouteView | null>(() => this.detail().route ?? null);

  /** Whether Complete has anything left to do (issue #294) — mirrors the hub
   * `CompleteService`'s no-op on an already-`done` chunk, so the dock withholds a
   * click that would write nothing. Every other status is completable, independent of
   * `pausable`/`route`: Complete does not hang off a live route the way Detach does. */
  protected readonly completable = computed<boolean>(() => !NOT_COMPLETABLE.has(this.detail().status));

  /** The unmet prerequisite's chunk id, from `ChunkDetail.blocked` (issue #461) — null
   * when the chunk carries no marking. */
  protected readonly blockedOn = computed<string | null>(() => this.detail().blocked?.prerequisite_chunk_id ?? null);

  /** The declare/release field's free-text value (D5, issue #461) — one field serves
   * both controls, since the board has no read that lists a chunk's standing edges for
   * a picker to offer. Prefilled from {@link blockedOn} when a marking stands; editable
   * from there, since Release may need to name an edge past the pre-claim window (no
   * marking) and Declare always names a chunk the marking never carries. */
  protected readonly prerequisiteInput = signal('');

  /** Which chunk is open, deduped by `computed`'s default equality — unlike reading
   * `detail()` directly, this does not change (and so does not re-run the prefill
   * effect below) on a same-chunk refetch, only on an actual chunk switch. A poll or
   * SSE-triggered refresh of the open chunk must never wipe an in-progress edit. */
  private readonly openChunkId = computed(() => this.detail().chunk_id);

  constructor() {
    effect(() => {
      this.openChunkId();
      this.prerequisiteInput.set(this.blockedOn() ?? '');
    });
  }

  /** Confirm, then emit `detach` for the container's mutation to fire. */
  protected onDetach(): void {
    if (!this.route()) return;
    const confirmed = globalThis.confirm(
      `Detach chunk ${this.detail().chunk_id} from its runner? This releases the runner; ` +
        `the chunk keeps its current status (this is not requeue).`,
    );
    if (!confirmed) return;
    this.detach.emit(this.detail().chunk_id);
  }

  /** Confirm, then emit `pauseChunk` for the container's mutation to fire (issue #46). */
  protected onPause(): void {
    if (this.pause() || !this.pausable()) return;
    const confirmed = globalThis.confirm(
      `Pause chunk ${this.detail().chunk_id}? This kills its active worker but keeps the ` +
        `claim (this is not detach); resume it later to pick the work back up.`,
    );
    if (!confirmed) return;
    this.pauseChunk.emit(this.detail().chunk_id);
  }

  /** Confirm, then emit `resumeChunk` for the container's mutation to fire (issue #46).
   * Guarded on the pause **fact**, never on `status`. */
  protected onResume(): void {
    if (!this.pause()) return;
    const confirmed = globalThis.confirm(
      `Resume chunk ${this.detail().chunk_id}? Its runner picks the work back up from ` +
        `where the pause stopped it.`,
    );
    if (!confirmed) return;
    this.resumeChunk.emit(this.detail().chunk_id);
  }

  /** Confirm, then emit `complete` for the container's mutation to fire (issue #294).
   * Unlike Detach/Pause/Resume, this is a one-way door: there is no un-complete verb,
   * and the confirmation says so. */
  protected onComplete(): void {
    if (!this.completable()) return;
    const confirmed = globalThis.confirm(
      `Complete chunk ${this.detail().chunk_id}? This marks it done by hand; there is no ` +
        `un-complete verb.`,
    );
    if (!confirmed) return;
    this.complete.emit(this.detail().chunk_id);
  }

  /** Confirm, then emit `declareDependency` for the container's mutation to fire (issue
   * #461). A blank field emits nothing — the hub has no chunk id to resolve. */
  protected onDeclareDependency(): void {
    const prerequisiteChunkId = this.prerequisiteInput().trim();
    if (!prerequisiteChunkId) return;
    const confirmed = globalThis.confirm(
      `Declare that chunk ${this.detail().chunk_id} depends on ${prerequisiteChunkId}?`,
    );
    if (!confirmed) return;
    this.declareDependency.emit({ chunkId: this.detail().chunk_id, prerequisiteChunkId });
  }

  /** Confirm, then emit `releaseDependency` for the container's mutation to fire (issue
   * #461). */
  protected onReleaseDependency(): void {
    const prerequisiteChunkId = this.prerequisiteInput().trim();
    if (!prerequisiteChunkId) return;
    const confirmed = globalThis.confirm(
      `Release chunk ${this.detail().chunk_id}'s dependency on ${prerequisiteChunkId}?`,
    );
    if (!confirmed) return;
    this.releaseDependency.emit({ chunkId: this.detail().chunk_id, prerequisiteChunkId });
  }
}

import {
  ChangeDetectionStrategy,
  Component,
  computed,
  input,
  output,
} from '@angular/core';
import { RouterLink } from '@angular/router';

import {
  WorkRefLink,
  type ChunkDetail,
  type ChunkStatus,
  type ChunkControl,
  type PauseView,
  type WorkRefView,
  type RouteView,
  runnerDisplayName,
  runnerTitle,
  KitButton,
  KitSwatch,
  ChunkActionsMenu,
} from 'fleet';
import { LensLink } from 'fleet/shell';

/**
 * The chunk detail dock's header — the chunk's identity (short name, work item, state,
 * and current node), the ⋮ actions menu ({@link ChunkActionsMenu}) wired to the
 * {@link control} port it is handed, and dismiss.
 *
 * Presentational: it emits `dismiss` and fires nothing itself. The status chip renders
 * {@link renderedStatus} rather than `detail().status` (`bzh:frontend-pending-override`).
 */
@Component({
  selector: 'app-chunk-detail-header',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [
    WorkRefLink,
    KitButton,
    KitSwatch,
    ChunkActionsMenu,
    RouterLink,
    LensLink,
  ],
  templateUrl: './chunk-detail-header.html',
  styleUrl: './chunk-detail-header.css',
})
export class ChunkDetailHeader {
  /** The chunk aggregate to render (identity, status, current node, pause, route). */
  readonly detail = input.required<ChunkDetail>();

  /** The chunk's project's color, drawn as the swatch beside its name; `null` draws the name alone. */
  readonly projectColor = input<string | null>(null);

  /** The chunk's status as the status chip renders it (`bzh:frontend-pending-override`).
   * Never consulted by the ⋮ menu's gates: those decide what the *next* click is
   * admissible to fire against the server-read status, which an in-flight mutation's own
   * predicted outcome must not perturb. */
  readonly renderedStatus = input.required<ChunkStatus>();

  /** The chunk detail route's path segments, before the chunk id — the longname link's target. */
  readonly linkBase = input<readonly string[]>(['/board', 'chunk']);

  /** The chunk-control port behind the ⋮ menu; `null` hides the menu. */
  readonly control = input<ChunkControl | null>(null);

  /** Run once the hub confirms a delete from the ⋮ menu. */
  readonly afterDelete = input<() => void>(() => undefined);

  /** Whether the header draws its close button — off where the screen has its own way back. */
  readonly closable = input(true);

  /** Emitted when the operator dismisses the dock. */
  readonly dismiss = output<void>();

  /** The chunk's work refs, for the header — each linked out to its source's web
   * address when the configured binding rendered one (a null `web_url` degrades to
   * plain text, no broken link). */
  protected readonly pointers = computed<readonly WorkRefView[]>(
    () => this.detail().work_refs ?? [],
  );

  /** The chunk's open operator pause, if any — who set it. Read off the
   * detail's `pause` fact, not `status`: a chunk both paused and parked on a question
   * derives `waiting_on_human`, so `status` alone would never surface it. */
  protected readonly pause = computed<PauseView | null>(
    () => this.detail().pause ?? null,
  );

  /** The chunk's live route, if any — the runner fact reads off it. */
  protected readonly route = computed<RouteView | null>(
    () => this.detail().route ?? null,
  );

  /** Each runner's host name by runner id — a runner the map omits shows no host. */
  readonly runnerHosts = input<ReadonlyMap<string, string>>(new Map());

  /** The display name of the runner holding the route, as `host/runner`, or `null` while none
   * does — the runner fact, and the ⋮ menu's `<runner>` slot. */
  protected readonly claimant = computed<string | null>(() => {
    const route = this.route();
    return route
      ? runnerDisplayName(
          route.runner_id,
          route.runner_name,
          this.runnerHosts().get(route.runner_id),
        )
      : null;
  });

  /** The runner fact's tooltip — the display name the fact may clip, then the runner's full id. */
  protected readonly claimantTitle = computed<string | null>(() => {
    const route = this.route();
    return route
      ? runnerTitle(
          route.runner_id,
          route.runner_name,
          this.runnerHosts().get(route.runner_id),
        )
      : null;
  });

  /** The node the chunk currently sits at — `current_node_name`, then
   * `current_node_id`, then an em dash. */
  protected readonly currentNodeName = computed<string>(
    () =>
      this.detail().current_node_name ?? this.detail().current_node_id ?? '—',
  );
}

import { ChangeDetectionStrategy, Component, input, output } from '@angular/core';
import type { runnerApi } from 'fleet';

import { KitAsyncState, type KitAsyncStateValue, KitPanel, KitPanelHeader } from 'fleet';

import { AgentRow } from './agent-row';
import { MachineDetail } from './chunk-detail';
import type { MachineChunkStatus } from './chunk-status';
import { ChunkRow } from './chunk-row';
import { EnvList } from './env-list';
import { LocalAsks } from './local-asks';
import { LocalInfo } from './local-info';
import type { MachineChunkRow } from './local-panel';

/**
 * The runner's machine-local panel's layout half (issue #80) — shaped like
 * the discovery mock's machine panel: a three-column grid over the runner's
 * local API, hub-free save for the rails that proxy through it — the
 * fleet-summary strip (`local-info`).
 *
 * - **Left (340px)** — liveness: the *active* leases (closed rows are history,
 *   not liveness — they live on the chunks list), each with a heartbeat
 *   freshness bar, over the held-environments rail, split 60/40.
 * - **Center (1fr)** — work: the chunks on this machine (one row per chunk,
 *   work-item-enriched, derived status in the hub board's colors) over the machine
 *   detail dock for the selected chunk's execution facts — the transcript and
 *   per-attempt selection moved to the runner-local chunk detail route
 *   (issue #318).
 * - **Right (330px)** — the machine's account of itself: the hub link
 *   (endpoint, reachability, last flush, buffer) and the open local asks. The
 *   local fact log moved to its own `/events` route (issue #313) — full
 *   width there rather than a rail-sized panel.
 *
 * Presentational only: it renders exactly the leases/chunks/selection it is
 * handed and emits `selectLease`/`selectChunk`; the derived-status fold and
 * the selection state live in the container ({@link LocalPanel}). Color
 * resolves through the shared design tokens (`fleet` library,
 * design/tokens.css), never hard-coded hex.
 *
 * Owns no titlebar (issue #325): the shared `BoardHeader` chrome, the pause
 * control/identity/profile-menu trailing cluster, and their narrow-tier
 * collapse all moved up to the app root's own `AppHeader`
 * (`../../runner/src/app/nav/app-header.ts`), the same shelf the hub board's
 * header sits on — mounted once, above the routed tab strip, rather than
 * nested inside this routed layout where it used to render under the tabs on
 * `/board` and not at all on `/events`.
 */
@Component({
  selector: 'local-panel-layout',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [AgentRow, MachineDetail, ChunkRow, EnvList, KitAsyncState, KitPanel, KitPanelHeader, LocalAsks, LocalInfo],
  templateUrl: './local-panel-layout.html',
  styleUrl: './local-panel-layout.css',
})
export class LocalPanelLayout {
  /** The active leases for the liveness rail. */
  readonly activeLeases = input.required<readonly runnerApi.LeaseView[]>();

  /** The leases rail's async triad state. */
  readonly leasesTriadState = input.required<KitAsyncStateValue>();

  /** The machine-chunks list's async triad state. */
  readonly chunksTriadState = input.required<KitAsyncStateValue>();

  /** The chunks pane's empty-state text — the container distinguishes
   * "nothing on this machine" from "the filter hid everything" (issue #134
   * review fix), so this layout renders whichever text it is handed rather
   * than a literal in the template. */
  readonly chunksEmptyText = input.required<string>();

  /** One row per chunk on this machine, pre-folded by the container **and**
   * already filtered per {@link showAllChunks} (issue #134) — the container
   * owns the fold, this renders exactly the rows it is handed. */
  readonly machineChunks = input.required<readonly MachineChunkRow[]>();

  /** The chunks list's "show all" checkbox state (issue #134) — unchecked
   * (the default) hides a chunk whose newest lease is closed; the container
   * derives {@link machineChunks} from this, so this is display-only here. */
  readonly showAllChunks = input.required<boolean>();

  /** The open-ask count for the asks panel's header note. */
  readonly openAskCount = input.required<number>();

  /** The `chunk_id` currently selected, or `null`. */
  readonly selectedChunkId = input.required<string | null>();

  /** The selected chunk's attempts (oldest → newest) — the detail dock reads
   * only the newest for its summary/status; per-attempt selection and the
   * transcript live on the chunk detail route instead (issue #318). */
  readonly selectedChunkLeases = input.required<readonly runnerApi.LeaseView[]>();

  readonly selectedStatus = input.required<MachineChunkStatus | null>();

  /** The open escalation for the selected chunk, when one exists. */
  readonly selectedEscalation = input.required<runnerApi.EscalationView | null>();

  /** Emitted with a lease id when the operator selects a lease row — the
   * lease rail and the chunks list share one selection, so this drives both. */
  readonly selectLease = output<string>();

  /** Emitted with a chunk id when the operator selects a chunk row. */
  readonly selectChunk = output<string>();

  /** Emitted with the checkbox's new checked state when the operator toggles
   * "show all" (issue #134). */
  readonly toggleShowAllChunks = output<boolean>();

  /** Emitted when the operator dismisses the detail dock (issue #185) via its
   * own close button — the container clears the selection. */
  readonly dismiss = output<void>();
}

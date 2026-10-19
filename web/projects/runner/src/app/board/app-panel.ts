import { ChangeDetectionStrategy, Component, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';
import { map } from 'rxjs';
import { asyncState, injectChunkUrlSelection, type runnerApi, ViewportService } from 'fleet';

import { injectPanelAsks, injectPanelEscalations, injectPanelLeases, injectPanelProjects, injectPanelRunners } from '../core/panel-reads';
import type { PanelLease } from '../core/panel-reads.model';

import type { MachineChunkStatus } from './chunk-list/chunk-status';
import {
  chunkLeasesFor,
  chunkStatusFor,
  escalationFor,
  type MachineChunkRow,
  machineChunkRows,
} from './app-panel.model';
import { LocalPanelLayout } from './app-panel-layout';
import type { BoardLayout } from './chunk-list/chunk-board';
import { LocalPanelMobile } from './app-panel-mobile';

/**
 * The panel's board — the data-orchestration container.
 * Owns the leases merged across the runners the filters leave
 * ({@link injectPanelLeases}, the Project filter applied), their open asks and
 * escalations off each runner's dashboard, the one derived-status fold
 * ({@link machineChunkRows}), and
 * the selection — which chunk is open, bound to the URL's `?chunk=` query
 * param so a link is shareable and a reload keeps its place.
 * Every panel below it (via {@link LocalPanelLayout}) is presentational or
 * owns just its own read.
 *
 * Owns no header state; see `../shell/nav/app-header.ts` and
 * `../shell/nav/mobile-titlebar.ts`.
 */
@Component({
  selector: 'app-panel',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [LocalPanelLayout, LocalPanelMobile],
  templateUrl: './app-panel.html',
  styleUrl: './app-panel.css',
})
export class LocalPanel {
  /** The page-level shell picker (`../docs/designs/mobile/README.md`'s
   * "adaptive shells over shared guts") — desktop renders the existing
   * three-column {@link LocalPanelLayout} unchanged; mobile renders
   * {@link LocalPanelMobile} instead, `@defer`-loaded so the desktop bundle
   * doesn't carry it. Owns neither the mobile bottom tab bar nor the viewport
   * override; see `../shell/nav/`. */
  protected readonly viewport = inject(ViewportService);

  protected readonly mode = this.viewport.mode;

  protected readonly leasesQuery = injectPanelLeases();

  /** The runners the board reads — the hub-link rail shows each. */
  protected readonly runners = injectPanelRunners().scoped;

  /** Every project the host serves — the rows take their project's name and tint from it. */
  private readonly projects = injectPanelProjects();

  private readonly asks = injectPanelAsks();
  private readonly escalations = injectPanelEscalations();

  /** The active + recently-closed leases, merged across runners; empty until the first read resolves. */
  private readonly leases = computed(() => this.leasesQuery.data() ?? []);

  /** The chunks pane's async triad state — the leases query's loading/error state, empty when no chunk has a row. */
  protected readonly chunksTriadState = computed(() => asyncState(this.leasesQuery, this.machineChunks().length === 0));

  /**
   * One row per chunk on this machine — every chunk a lease names, so a live lease
   * always has a row, carrying its worker inline: the chunk's newest lease (the server
   * orders actives first, then the recent-closed block, so the first lease
   * seen per `chunk_id` is the freshest attempt) plus every attempt of the
   * chunk and the derived status — folded once here, handed to the row and the
   * detail dock alike. Each row's `leases` is ordered oldest → newest, so
   * `lease` (the summary/status subject) is that list's own newest entry.
   */
  protected readonly machineChunks = computed<MachineChunkRow[]>(() =>
    machineChunkRows(this.leases(), this.projects()),
  );


  private readonly route = inject(ActivatedRoute);
  private readonly router = inject(Router);

  /** The chunks' layout, the URL's `?layout=list` — absent is the lane board, as on the hub's board. */
  protected readonly layout = toSignal(
    this.route.queryParamMap.pipe(map((params): BoardLayout => (params.get('layout') === 'list' ? 'list' : 'board'))),
    { initialValue: 'board' as BoardLayout },
  );

  /** Switch the layout by writing the URL, keeping every other param (the open chunk among them). */
  protected setLayout(layout: BoardLayout): void {
    void this.router.navigate([], {
      relativeTo: this.route,
      queryParams: { layout: layout === 'list' ? 'list' : null },
      queryParamsHandling: 'merge',
    });
  }

  /** The open-ask count for the asks panel's header note. */
  protected readonly openAskCount = computed(() => this.asks.data().length);

  /** What is open in the panel, held in the URL's `?chunk=` via the shared
   * {@link injectChunkUrlSelection} — the router coupling lives there, not
   * here. Carries no `attempt` selection: this panel neither reads nor
   * clears an `attempt` query param, and writes none itself. */
  private readonly selection = injectChunkUrlSelection();

  /**
   * The `chunk_id` currently selected. A lease row selects its chunk too
   * ({@link selectChunk}) — the lease rail and the chunks list share one
   * selection, reflected on both.
   */
  protected readonly selectedChunkId = this.selection.chunkId;

  /** Write a chunk selection to the URL, clearing any stale `attempt` (attempt
   * lease ids are chunk-specific, so a new chunk defaults to its newest) — a lease row
   * selects its chunk, so the detail dock defaults to the chunk's newest attempt. */
  protected selectChunk(chunkId: string): void {
    this.selection.select(chunkId);
  }

  /** Clear the selection entirely — the mobile shell's back affordance, which
   * closes its drill-down by removing what the detail screen renders off. A
   * plain navigation like any other selection write, so the device back button
   * and this button walk the same history. Desktop has no caller: its dock
   * simply falls back to `SELECT A CHUNK`. */
  protected clearSelection(): void {
    this.selection.select(null);
  }

  /**
   * The selected chunk's attempts (oldest → newest) — what the detail dock's
   * summary/status renders off the newest. Empty when nothing is selected.
   */
  protected readonly selectedChunkLeases = computed<readonly PanelLease[]>(() =>
    chunkLeasesFor(this.machineChunks(), this.selectedChunkId()),
  );

  protected readonly selectedStatus = computed<MachineChunkStatus | null>(() =>
    chunkStatusFor(this.machineChunks(), this.selectedChunkId()),
  );

  /** The open escalation for the selected chunk, when one exists — carries the resume command. */
  protected readonly selectedEscalation = computed<runnerApi.EscalationView | null>(() =>
    escalationFor(this.escalations.data(), this.selectedChunkLeases().at(-1)?.owner.runnerId ?? null, this.selectedChunkId()),
  );
}

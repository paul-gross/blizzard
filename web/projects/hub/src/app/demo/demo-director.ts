import { Injectable, inject, signal } from '@angular/core';
import { Router } from '@angular/router';
import { type ArtifactView, injectHubChunkDetailQuery, injectHubChunksQuery, sortArtifacts } from 'fleet';

import { type DemoConfig, demoQueryParams, readDemoConfig } from './demo-config';
import { DemoKiosk } from './demo-kiosk';
import { scrollersIn, slowScrollToBottom } from './demo-scroll';
import { DemoAborted, sleep, sleepUntil, waitFor } from './demo-timing';

/**
 * The unattended demo pilot — the thing `?demo=true` hands the board to.
 *
 * One cycle, repeated forever, `swapChunkMs` long (2 minutes by default):
 *
 * 1. pick a chunk at random off the live fleet list and open it on the board
 *    (`/board?chunk=…`), then ease the detail dock's panes down to their bottom
 *    over `boardScrollMs` — long enough to read, slow enough not to read as
 *    motion;
 * 2. descend into that chunk's own page on the Artifacts tab
 *    (`/board/chunk/:id?tab=artifacts`) and tour its store: a random artifact
 *    every `artifactMs`, each one scrolled from top to bottom across its whole
 *    dwell;
 * 3. at the cycle's end, hand back to step 1 with a different chunk — the swap
 *    boundary, and also the one moment a kiosk reload is allowed to happen
 *    (see {@link DemoKiosk}).
 *
 * **State is in memory, not in the URL.** The config latches once at
 * construction (`demo-config.ts`) and the loop below is a plain async function
 * holding its own place, so a navigation that drops `?demo` cannot switch the
 * demo off. The params are re-emitted onto every navigation anyway — not for
 * this class's benefit, but so the URL on the wall is always one a kiosk reload
 * resumes from.
 *
 * A container in the `bzh:frontend-container-presentational` sense: it owns the
 * two reads it needs and drives the router, and no component knows it exists.
 * The DOM it touches is read-only — it scrolls regions, it never synthesizes
 * clicks — so nothing here can fire an operator action against a real fleet.
 */
@Injectable({ providedIn: 'root' })
export class DemoDirector {
  private readonly router = inject(Router);
  private readonly kiosk = inject(DemoKiosk);

  /** The config {@link start} was handed. The app root latches it at *its* own
   * construction (`app.ts`), which is earlier than this class exists — this
   * whole module is lazy-loaded, and by the time it arrives an auth redirect may
   * already have rewritten the URL that carried the params. */
  private config: DemoConfig = readDemoConfig('');

  private readonly chunksQuery = injectHubChunksQuery();

  /** The chunk the tour is on — drives the detail read the artifact list comes from. */
  private readonly touring = signal<string | null>(null);
  private readonly detailQuery = injectHubChunkDetailQuery(() => this.touring());

  private controller: AbortController | null = null;

  /** Start the tour under `config`. Idempotent — the app root calls this from a
   * render effect that re-runs on every auth-state settle. */
  start(config: DemoConfig): void {
    if (!config.enabled || this.controller !== null) return;
    this.config = config;
    this.controller = new AbortController();
    const signal = this.controller.signal;
    void this.kiosk.begin().then(() => this.run(signal));
  }

  /** Stop the tour, drop every pending wait, and give the screen back. */
  stop(): void {
    this.controller?.abort();
    this.controller = null;
    void this.kiosk.end();
  }

  /** The forever loop. A cycle that throws for any reason other than teardown
   * costs one dwell and the next cycle picks a different chunk — a demo screen
   * that stops on the first hub blip is worse than one that skips a slide. */
  private async run(signal: AbortSignal): Promise<void> {
    for (;;) {
      try {
        await this.runCycle(signal);
      } catch (error) {
        if (signal.aborted || error instanceof DemoAborted) return;
        await sleep(5000, signal).catch(() => undefined);
        if (signal.aborted) return;
      }
    }
  }

  private async runCycle(signal: AbortSignal): Promise<void> {
    const chunkId = await waitFor(() => this.pickChunk(), 60_000, signal);
    if (chunkId === null) {
      // An empty (or unresolved) fleet — wait a beat and ask again rather than
      // spin the router against nothing.
      await sleep(10_000, signal);
      return;
    }

    const cycleEnd = performance.now() + this.config.swapChunkMs;
    this.touring.set(chunkId);

    await this.go(['/board'], { chunk: chunkId, tab: null, artifact: null });
    const dock = await waitFor(() => showing('chunk-detail', 'detail-id', chunkId), 15_000, signal);

    // The board holds for the **whole** `boardScrollMs`, whether or not the dock
    // has anywhere to scroll. A short chunk's dock fits without overflowing, and
    // stepping straight off it turned the board into a flicker between two chunk
    // pages rather than a beat of its own — the dwell is the point, the scroll is
    // just what fills it. Timed from the dock's arrival, so a slow read costs the
    // demo waiting time, never scrolling time.
    const boardEnd = performance.now() + this.config.boardScrollMs;
    if (dock !== null) await slowScrollToBottom(scrollersIn(dock), this.config.boardScrollMs, signal);
    await sleepUntil(boardEnd, signal);

    // Belt to the config clamp's braces (`MAX_BOARD_SHARE`): descend only if the
    // cycle has time left to spend down there. Navigating with nothing remaining
    // would show the chunk page for a single frame before the swap — the flicker
    // the board dwell exists to prevent, reintroduced one step later.
    if (performance.now() < cycleEnd) {
      await this.go(['/board/chunk', chunkId], { chunk: null, tab: 'artifacts', artifact: null });
      await this.tourArtifacts(chunkId, cycleEnd, signal);
    }

    if (await this.kiosk.reloadIfStale(this.config.maxUptimeMs)) {
      // The document is being replaced — park here rather than start a cycle
      // whose navigations would race the reload.
      await sleep(60_000, signal);
    }
  }

  /**
   * Walk the touring chunk's artifact store until `cycleEnd`: a random entry
   * every `artifactMs`, each scrolled across its full dwell.
   *
   * A chunk with no artifacts ends the cycle **early** rather than staring at an
   * empty viewer for the rest of the cycle — the swap to the next chunk is
   * the more interesting thing to show. A chunk with exactly one artifact keeps
   * showing it (re-scrolled each dwell), which is the honest rendering of what
   * that chunk has.
   */
  private async tourArtifacts(chunkId: string, cycleEnd: number, signal: AbortSignal): Promise<void> {
    let shown: string | null = null;

    while (performance.now() < cycleEnd) {
      const artifacts = await waitFor(() => this.artifacts(), 10_000, signal);
      if (artifacts === null || artifacts.length === 0) {
        // Nothing to tour. Hold the chunk page for one dwell so the swap reads as
        // a slide rather than a flicker, then let the cycle end early — the next
        // chunk is the more interesting thing to show.
        await sleep(Math.min(this.config.artifactMs, Math.max(0, cycleEnd - performance.now())), signal);
        return;
      }

      const dwellEnd = Math.min(performance.now() + this.config.artifactMs, cycleEnd);
      const key = pickAnother(artifacts, shown);
      shown = key;

      await this.go(['/board/chunk', chunkId], { tab: 'artifacts', artifact: key });
      const body = await waitFor(() => showing('artifacts-tab-artifact', 'artifacts-tab-artifact-key', key), 8000, signal);
      if (body !== null) {
        await slowScrollToBottom(scrollersIn(body), Math.max(0, dwellEnd - performance.now()), signal);
      }
      await sleepUntil(dwellEnd, signal);
    }
  }

  /** The touring chunk's artifact store in display order, or `null` until the
   * detail read for *this* chunk has resolved. */
  private artifacts(): readonly ArtifactView[] | null {
    const detail = this.detailQuery.data();
    if (detail === undefined || detail.chunk_id !== this.touring()) return null;
    return sortArtifacts(detail.artifacts ?? []);
  }

  /** A random chunk off the live fleet list, or `null` before the read lands. */
  private pickChunk(): string | null {
    const chunks = this.chunksQuery.data() ?? [];
    if (chunks.length === 0) return null;
    return chunks[Math.floor(Math.random() * chunks.length)].chunk_id;
  }

  /** Navigate, carrying the demo params. Params named `null` are cleared, so a
   * route's leftovers (`?artifact` from the previous chunk) never ride along. */
  private async go(commands: readonly string[], params: Record<string, string | null>): Promise<void> {
    await this.router.navigate([...commands], {
      queryParams: { ...params, ...demoQueryParams(this.config) },
    });
  }
}

/** A random entry that is not `current`, falling back to `current` when the
 * store holds nothing else to move to. */
function pickAnother(artifacts: readonly ArtifactView[], current: string | null): string {
  const others = artifacts.filter((artifact) => artifact.key !== current);
  const pool = others.length > 0 ? others : artifacts;
  return pool[Math.floor(Math.random() * pool.length)].key;
}

/**
 * The region named by `regionTestid`, but **only once it is rendering the thing
 * the director just asked for** — `labelTestid`'s text inside it reads `expected`.
 *
 * The identity check is what makes the waits honest. A router navigation
 * resolves before Angular has re-rendered, and the previous chunk's dock (or the
 * previous artifact's body) is still in the DOM at that instant — a plain
 * "wait for the selector" would hand back the outgoing node and the director
 * would spend its whole dwell scrolling an element about to be destroyed.
 *
 * These four handles are a **contract with components this file does not own**,
 * and breaking it fails quietly (the wait times out, the scroll is skipped, the
 * screen holds still). Each half is pinned on the producing side so a rename
 * fails where it is made:
 *
 * - `chunk-detail` / `detail-id` — by the browser tier, `tests/e2e/test_board_browser_e2e.py`;
 * - `artifacts-tab-artifact` / `artifacts-tab-artifact-key` — by
 *   `board/chunk/chunk-artifacts-tab.spec.ts`, which exists partly for this.
 *
 * Do not rely on this spec's own stand-ins for that: they would only prove the
 * director agrees with itself.
 */
function showing(regionTestid: string, labelTestid: string, expected: string): HTMLElement | null {
  for (const region of document.querySelectorAll<HTMLElement>(`[data-testid="${regionTestid}"]`)) {
    const label = region.querySelector(`[data-testid="${labelTestid}"]`);
    if (label?.textContent?.trim() === expected) return region;
  }
  return null;
}

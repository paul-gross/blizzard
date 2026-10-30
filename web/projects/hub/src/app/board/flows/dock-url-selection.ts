import { type Signal, computed, inject } from '@angular/core';
import { toSignal } from '@angular/core/rxjs-interop';
import { ActivatedRoute, Router } from '@angular/router';

import { injectChunkUrlSelection } from 'fleet';

import { type DockSelection, dockParams, dockSelection } from './dock-selection.model';

/**
 * The board dock's selection, held in the URL: `?chunk=` opens a chunk, `?flow=` a flow's record, and every pick writes
 * one while clearing the other, so the two never stand together. It reads `?chunk=` through `injectChunkUrlSelection`,
 * and the URL is the single source of truth as there: a link is copyable, a reload keeps its place, and back/forward
 * walk the picks.
 */
export interface DockUrlSelection {
  /** The `?chunk=` the dock shows, or `null` — unvalidated, as the URL names it. */
  readonly chunkId: Signal<string | null>;
  /** The `?flow=` the dock shows, or `null` — `null` whenever a chunk is named. */
  readonly flowId: Signal<string | null>;
  readonly selection: Signal<DockSelection>;
  /** Open a chunk — or close the dock with `null` — clearing any flow. */
  selectChunk(chunkId: string | null): void;
  /** Open a flow's record — or close the dock with `null` — clearing any chunk. */
  selectFlow(flowId: string | null): void;
}

/** Bind {@link DockUrlSelection} to the current route. Call from an injection context. */
export function injectDockUrlSelection(): DockUrlSelection {
  const route = inject(ActivatedRoute);
  const router = inject(Router);
  const chunk = injectChunkUrlSelection();
  const params = toSignal(route.queryParamMap, { initialValue: route.snapshot.queryParamMap });
  const selection = computed(() => dockSelection(chunk.chunkId(), params().get('flow')));
  const write = (queryParams: { chunk: string | null; flow: string | null }): void => {
    void router.navigate([], { relativeTo: route, queryParams, queryParamsHandling: 'merge' });
  };
  return {
    chunkId: chunk.chunkId,
    flowId: computed(() => {
      const current = selection();
      return current.view === 'flow' ? current.flowId : null;
    }),
    selection,
    selectChunk: (chunkId) => write(dockParams({ chunk: chunkId })),
    selectFlow: (flowId) => write(dockParams({ flow: flowId })),
  };
}

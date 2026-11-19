import { type Provider, computed, signal } from '@angular/core';
import { type ChunkPageActions, type DaemonTarget, hubClient, provideChunkPageDaemon } from 'fleet';
import { injectAwaitingHumanService } from '../chunks/awaiting-human.service';
import { injectHubChunkControl } from '../chunks/hub-chunk-control';
import { hasPermission, injectMeQuery } from '../../core/auth/me.query';
import { injectProjectsQuery } from '../../core/projects.query';
import { injectShowProjectColors, projectColorOf } from '../../core/project-lens-colors';

/** The graphs view's own path segments — the target every hub composition site links a
 * graph badge to. */
const GRAPH_LINK_BASE: readonly string[] = ['/graphs'];

/** The events view's own path segments — the chunk page links a chunk's own events
 * there, filtered by `?chunk=`. */
const EVENTS_LINK_BASE: readonly string[] = ['/events'];

/**
 * The hub's operator-action port for the shared chunk page — its `/api/me` permissions
 * and the awaiting-human saves (answer, resolve). Called from the page's
 * own injection context, so every visit gets fresh notice channels. Both channels
 * clear at the start of every action, so a stale outcome never sits beside an unrelated
 * failure; a lost answer or resolve race is said on the outcome channel.
 */
export function injectHubChunkActions(): ChunkPageActions {
  const meQuery = injectMeQuery();
  const actionError = signal<string | null>(null);
  const actionOutcome = signal<string | null>(null);

  function beginAction(): void {
    actionError.set(null);
    actionOutcome.set(null);
  }

  return {
    // `null`/pending identity resolves every permission to `false` — hidden until confirmed.
    canAnswer: computed(() => hasPermission(meQuery.data(), 'question:answer')),
    canResolve: computed(() => hasPermission(meQuery.data(), 'gate:resolve')),
    canReadTranscripts: computed(() => hasPermission(meQuery.data(), 'transcript:read')),
    actionError: actionError.asReadonly(),
    actionOutcome: actionOutcome.asReadonly(),
    graphLinkBase: GRAPH_LINK_BASE,
    eventsLinkBase: EVENTS_LINK_BASE,
    control: injectHubChunkControl({ beginAction, fail: (message) => actionError.set(message) }),
    awaitingHuman: injectAwaitingHumanService({ begin: beginAction, outcome: (message) => actionOutcome.set(message) }),
  };
}

/** The board view's own path segments — the chunk page links back there, and its chunk
 * links sit under `/board/chunk`. */
const BOARD_LINK_BASE: readonly string[] = ['/board'];

/** The hub's own target for the shared chunk page's reads. */
const HUB_TARGET: DaemonTarget = { plane: 'hub', client: hubClient };

/** The hub's daemon for the shared chunk page — its own target and board, and the
 * operator-action port above. */
export const HUB_CHUNK_PAGE_PROVIDERS: Provider[] = [
  provideChunkPageDaemon(() => {
    const projects = injectProjectsQuery();
    const showProjectColors = injectShowProjectColors();
    return {
      target: () => HUB_TARGET,
      boardLinkBase: () => BOARD_LINK_BASE,
      actions: injectHubChunkActions,
      projectColor: (projectId: string) =>
        projectColorOf(showProjectColors(), projects.data(), projectId),
    };
  }),
];

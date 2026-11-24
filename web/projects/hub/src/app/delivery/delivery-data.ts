import { computed, type Signal } from '@angular/core';

import { injectNowSignal, injectRunnerHostNames, type KitAsyncStateValue } from 'fleet';
import { injectBoardWorkItemDomainsQuery } from '../core/work-items/board-work-items.query';
import { injectProjectDomainsQuery } from '../core/projects.query';
import { deliveryChunkIds, deliveryList, type DeliveryList, deliveryState, flowIds } from './delivery.model';
import type { DeliveryFlow, DeliveryRepository } from './delivery.domain';
import { injectDeliveryChunksQuery, injectDeliveryFlowsQuery, injectDeliveryRepositoriesQuery, injectFlowFactsQuery } from './delivery.query';
import { injectShowProjectColors, projectColorMap } from '../core/project-lens-colors';

/** The list's clock: the reading clock floored to the minute, so the join reruns once a minute rather than every
 * tick — only a bounce's day-long window reads it. */
const LIST_CLOCK_MS = 60_000;

/** Everything a Delivery list reads, joined: the desktop page and the phone's list share it. */
export interface DeliveryData {
  readonly flows: Signal<readonly DeliveryFlow[]>;
  readonly repositories: Signal<readonly DeliveryRepository[]>;
  /** The list's read state ({@link deliveryState}). */
  readonly state: Signal<KitAsyncStateValue>;
  readonly list: Signal<DeliveryList>;
  /** The reading clock, for the dates and ages a view draws. */
  readonly now: Signal<number>;
}

/** Read the tenant's active flows and their newest record facts, and the board's chunk list, work items and runner
 * hosts, joined by {@link deliveryList}. Call in an injection context. */
export function injectDeliveryData(): DeliveryData {
  const flowsQuery = injectDeliveryFlowsQuery();
  const repositoriesQuery = injectDeliveryRepositoriesQuery();
  const chunksQuery = injectDeliveryChunksQuery();
  const projectsQuery = injectProjectDomainsQuery();
  const showProjectColors = injectShowProjectColors();
  const runnerHosts = injectRunnerHostNames();
  const now = injectNowSignal();
  const listClock = computed(() => Math.floor(now() / LIST_CLOCK_MS) * LIST_CLOCK_MS);

  const repositories = computed(() => repositoriesQuery.data() ?? []);
  const flows = computed(() => flowsQuery.data() ?? []);
  const chunks = computed(() => chunksQuery.data() ?? []);
  const flowFacts = injectFlowFactsQuery(() => flowIds(flows()));
  const workItems = injectBoardWorkItemDomainsQuery(() => deliveryChunkIds(chunks(), flowFacts().facts));

  const list = computed(() =>
      deliveryList({
        flows: flows(),
        repositories: repositories(),
        chunks: chunks(),
        facts: flowFacts().facts,
        workItems: workItems(),
        projectColors: projectColorMap(showProjectColors(), projectsQuery.data()),
        runnerHosts: runnerHosts(),
        now: listClock(),
      }),
  );

  return {
    flows,
    repositories,
    now,
    list,
    state: computed(() => deliveryState([flowsQuery, chunksQuery], flowFacts(), list())),
  };
}

import { NgTemplateOutlet } from '@angular/common';
import { ChangeDetectionStrategy, Component, computed } from '@angular/core';

import { injectChunkUrlSelection, injectNowSignal, injectRunnerHostNames, KitSelectRow, KitSkeleton } from 'fleet';
import { LaneIcon } from '../board/lane-icon/lane-icon';
import { injectActiveFlowsQuery } from '../board/flows/flows.query';
import { injectLaneRepositoriesQuery } from '../board/flows/repositories.query';
import { injectBoardWorkItemsQuery } from '../board/work-items/board-work-items.query';
import { injectHubBoardChunksQuery } from '../core/chunks.query';
import { injectProjectsQuery } from '../core/projects.query';
import { injectHubGraphNamesQuery } from '../graphs/graphs.query';
import { DeliveryChunk } from './delivery-chunk';
import { DeliveryDrawer } from './delivery-drawer';
import { deliveryChunkIds, deliveryDetailIds, deliveryGraphIds, deliveryList, findRow, flowIds, STAGES, toggledSelection } from './delivery.model';
import { skeletonRows } from './delivery-skeleton.model';
import { injectChunkBouncesQuery, injectFlowFactsQuery } from './delivery.query';

/**
 * The `/delivery` route — the desktop Delivery tab: one list, a row per chunk — the chunks delivering, newest first,
 * then the chunks delivered — over a drawer for the row picked. Reads the tenant's active flows and their newest
 * record facts, and the board's chunk list, work items, graph names and runner hosts; {@link deliveryList} joins
 * them. The pick is held in the URL's `?chunk=`, as the board's dock holds its own; picking the picked row again, or
 * Escape, clears it.
 */
@Component({
  selector: 'app-delivery-page',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DeliveryChunk, DeliveryDrawer, KitSelectRow, KitSkeleton, LaneIcon, NgTemplateOutlet],
  templateUrl: './delivery-page.html',
  styleUrl: './delivery-page.css',
  host: { '(document:keydown.escape)': 'clear()' },
})
export class DeliveryPage {
  private readonly flowsQuery = injectActiveFlowsQuery();
  private readonly repositoriesQuery = injectLaneRepositoriesQuery();
  private readonly chunksQuery = injectHubBoardChunksQuery();
  private readonly projectsQuery = injectProjectsQuery();
  private readonly runnerHosts = injectRunnerHostNames();
  private readonly now = injectNowSignal();
  private readonly selection = injectChunkUrlSelection();

  protected readonly repositories = computed(() => this.repositoriesQuery.data() ?? []);
  protected readonly flows = computed(() => this.flowsQuery.data() ?? []);
  private readonly chunks = computed(() => this.chunksQuery.data() ?? []);
  private readonly flowFacts = injectFlowFactsQuery(() => flowIds(this.flows()));
  private readonly workItems = injectBoardWorkItemsQuery(() => deliveryChunkIds(this.chunks(), this.flowFacts().facts));
  private readonly chunkBounces = injectChunkBouncesQuery(() => deliveryDetailIds(this.chunks()));
  private readonly graphNames = injectHubGraphNamesQuery(() => deliveryGraphIds(this.chunks()));

  protected readonly stages = STAGES;
  /** The loading placeholders, drawn once. */
  protected readonly skeletons = skeletonRows();
  protected readonly selectedId = this.selection.chunkId;
  protected readonly pending = computed(() => this.flowsQuery.isPending() || this.chunksQuery.isPending() || this.flowFacts().pending);
  protected readonly failed = computed(() => this.flowsQuery.isError() || this.chunksQuery.isError());

  protected readonly list = computed(() =>
    deliveryList({
      flows: this.flows(),
      bounces: this.chunkBounces(),
      repositories: this.repositories(),
      chunks: this.chunks(),
      facts: this.flowFacts().facts,
      workItems: this.workItems(),
      projects: this.projectsQuery.data() ?? [],
      graphNames: this.graphNames(),
      runnerHosts: this.runnerHosts(),
      now: this.now(),
    }),
  );

  /** The picked row, while the list holds it. */
  protected readonly selectedRow = computed(() => findRow(this.list(), this.selectedId()));

  protected pick(chunkId: string): void {
    this.selection.select(toggledSelection(this.selectedId(), chunkId));
  }

  protected clear(): void {
    if (this.selectedId() !== null) this.selection.select(null);
  }
}

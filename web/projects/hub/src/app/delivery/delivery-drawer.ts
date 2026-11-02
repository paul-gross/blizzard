import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { asyncState, type FlowView, injectHubChunkDetailQuery, injectNowSignal, injectRunnerHostNames, type RepositorySummary } from 'fleet';
import { injectProjectsQuery } from '../core/projects.query';
import { injectShowProjectColors, openProjectColor } from '../core/project-lens-colors';
import { deliveryDrawerView } from './delivery-history.model';
import { DeliveryDrawerView } from './delivery-drawer-view';

/**
 * The Delivery page's drawer for the picked row: reads the chunk's own detail, which the board's dock shares, joins it
 * with the flows and repositories handed in ({@link deliveryDrawerView}), and hands the result to the
 * {@link DeliveryDrawerView}.
 */
@Component({
  selector: 'app-delivery-drawer',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [DeliveryDrawerView],
  templateUrl: './delivery-drawer.html',
  styleUrl: './delivery-drawer.css',
})
export class DeliveryDrawer {
  readonly chunkId = input.required<string>();
  readonly flows = input.required<readonly FlowView[]>();
  readonly repositories = input.required<readonly RepositorySummary[]>();
  /** Whether the drawer draws its close button — off on the phone's screen, whose back row returns to the list. */
  readonly closable = input(true);
  /** Emitted when the operator closes the drawer. */
  readonly dismiss = output<void>();

  private readonly detailQuery = injectHubChunkDetailQuery(() => this.chunkId());
  private readonly now = injectNowSignal();
  private readonly projectsQuery = injectProjectsQuery();
  private readonly showProjectColors = injectShowProjectColors();

  protected readonly runnerHosts = injectRunnerHostNames();
  protected readonly detail = computed(() => this.detailQuery.data());
  protected readonly state = computed(() => asyncState(this.detailQuery, false));
  protected readonly projectColor = computed(() => openProjectColor(this.showProjectColors(), this.detail(), this.projectsQuery.data()));
  protected readonly view = computed(() => deliveryDrawerView(this.detail(), this.flows(), this.repositories(), this.now()));
}

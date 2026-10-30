import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';

import { asyncState, injectNowSignal, type KitAsyncStateValue, type WorkItemsView } from 'fleet';
import { flowRecordRows, flowRecordTitle } from './flow-record.model';
import { FlowRecordView } from './flow-record-view';
import { injectFlowQuery, injectFlowRecordQuery } from './flows.query';

/**
 * The flow record **container** for the board's dock: reads one flow and its record a page at a time, and renders
 * {@link FlowRecordView} over them. The board mounts it while the URL's `?flow=` names a flow.
 */
@Component({
  selector: 'app-flow-record',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [FlowRecordView],
  templateUrl: './flow-record.html',
  styleUrl: './flow-record.css',
})
export class FlowRecord {
  /** The flow whose record the dock shows. */
  readonly flowId = input.required<string>();

  /** The board's work items by chunk id, which the record's chunks take their titles from. */
  readonly workItems = input<ReadonlyMap<string, WorkItemsView>>(new Map());

  readonly dismiss = output<void>();
  readonly selectChunk = output<string>();

  private readonly flowQuery = injectFlowQuery(() => this.flowId());
  private readonly recordQuery = injectFlowRecordQuery(() => this.flowId());
  private readonly now = injectNowSignal();

  protected readonly title = computed(() => flowRecordTitle(this.flowId(), this.flowQuery.data()));
  protected readonly rows = computed(() => flowRecordRows(this.recordQuery.data()?.pages, this.workItems(), this.now()));
  protected readonly state = computed<KitAsyncStateValue>(() => asyncState(this.recordQuery, this.rows().length === 0));
  protected readonly hasOlder = computed(() => this.recordQuery.hasNextPage());
  protected readonly olderPending = computed(() => this.recordQuery.isFetchingNextPage());

  protected onOlder(): void {
    void this.recordQuery.fetchNextPage();
  }
}

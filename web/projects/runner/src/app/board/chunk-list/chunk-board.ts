import { ChangeDetectionStrategy, Component, computed, input, output } from '@angular/core';
import { KitAsyncState, type KitAsyncStateValue, KitPanel, KitPanelHeader, KitTabs, type KitTabOption } from 'fleet';

import { BOARD_LANES, type BoardLane, type MachineChunkRow } from '../../core/chunk-rows.model';
import { rankedRows } from '../app-panel.model';
import { ChunkRow } from './chunk-row';

/** Which layout the chunks render in — the lane board or the ranked list. */
export type BoardLayout = 'board' | 'list';

/** The Board/List switch's options. */
const LAYOUT_TABS: readonly KitTabOption[] = [
  { value: 'board', label: 'Board', testid: 'board-layout-board' },
  { value: 'list', label: 'List', testid: 'board-layout-list' },
];

/**
 * The chunks on the runners read, as the hub's board lays its chunks out: a Board/List switch
 * in the panel header over either the three lanes ({@link BOARD_LANES} — needs human, in
 * progress, done), one card per chunk, or one ranked list, a row per chunk, lane by lane.
 * Each card and row carries the chunk's live worker inline.
 *
 * Presentational: the container folds, filters and selects; this renders the rows it is
 * handed and emits what the operator picks. `stacked` lays the lanes one under another for
 * a phone's single column instead of side by side.
 */
@Component({
  selector: 'app-chunk-board',
  changeDetection: ChangeDetectionStrategy.OnPush,
  imports: [ChunkRow, KitAsyncState, KitPanel, KitPanelHeader, KitTabs],
  templateUrl: './chunk-board.html',
  styleUrl: './chunk-board.css',
  host: { '[class.stacked]': 'stacked()' },
})
export class ChunkBoard {
  /** One row per chunk, already folded and filtered by the container. */
  readonly rows = input.required<readonly MachineChunkRow[]>();

  /** Which layout renders. */
  readonly layout = input<BoardLayout>('board');

  /** The rows' async triad state. */
  readonly state = input.required<KitAsyncStateValue>();

  /** The empty-state text, the container's to word. */
  readonly emptyText = input('NO CHUNKS ON THESE RUNNERS');

  /** The `chunk_id` currently selected, or `null`. */
  readonly selectedChunkId = input<string | null>(null);

  /** Lays the lanes one under another instead of side by side. */
  readonly stacked = input(false);

  readonly selectChunk = output<string>();
  readonly layoutChange = output<BoardLayout>();

  protected readonly layoutTabs = LAYOUT_TABS;
  protected readonly lanes = BOARD_LANES;

  /** The rows by lane, each lane in the container's order. */
  private readonly byLane = computed(() => {
    const lanes = new Map<BoardLane, MachineChunkRow[]>(BOARD_LANES.map((lane) => [lane.key, []]));
    for (const row of this.rows()) lanes.get(row.lane)?.push(row);
    return lanes;
  });

  protected rowsFor(lane: BoardLane): readonly MachineChunkRow[] {
    return this.byLane().get(lane) ?? [];
  }

  /** The list view's rows, ranked lane by lane. */
  protected readonly ranked = computed(() => rankedRows(this.rows()));

  protected chooseLayout(value: string): void {
    this.layoutChange.emit(value === 'list' ? 'list' : 'board');
  }
}

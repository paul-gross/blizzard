import { ChangeDetectionStrategy, Component, input } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { BoardList } from '../board-list/board-list';
import { BoardColumn } from '../board-shell/board-column';
import { BoardShell } from '../board-shell/board-shell';
import { GlanceBoard } from '../glance/glance-board';
import { GlanceView } from '../glance/glance-view';
import { NodeStrip } from './node-strip';

/** The strip's inputs with none of its reads, so a presentational spec needs no query client to host it. */
@Component({
  selector: 'app-node-strip',
  changeDetection: ChangeDetectionStrategy.OnPush,
  templateUrl: './node-strip.stub.html',
  host: { 'data-testid': 'node-strip-stub', '[attr.data-chunk]': 'chunkId()', '[attr.data-node]': 'currentNodeId()' },
})
export class NodeStripStub {
  readonly chunkId = input.required<string>();
  readonly graphId = input.required<string | null>();
  readonly currentNodeId = input<string | null>(null);
}

/** Swap {@link NodeStrip} for {@link NodeStripStub} in every surface that hosts it; call before the fixture is created.
 *
 * Each host's own host is recompiled too: a component keeps the template it first rendered, with the definitions of
 * the components inside it as they stood then, so a board or glance rendered unstubbed earlier in the same run would
 * otherwise keep drawing the real strip. */
export function stubNodeStrip(): void {
  for (const host of [BoardColumn, BoardList, GlanceView]) {
    TestBed.overrideComponent(host, { remove: { imports: [NodeStrip] }, add: { imports: [NodeStripStub] } });
  }
  for (const outer of [BoardShell, GlanceBoard]) {
    TestBed.overrideComponent(outer, { add: { imports: [] } });
  }
}

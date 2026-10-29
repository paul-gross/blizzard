import { provideZonelessChangeDetection, type Type } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { ChunkDetail, FlowFactView } from '../../api/hub';
import { chunkDetail, transitionView } from 'fleet/testing';
import { ChunkTimeline } from './chunk-timeline';
import { deriveHistoryRows, rowChoice, rowMark } from './chunk-timeline-rows';
import { ChunkTimelineSelection } from './chunk-timeline-selection';

const fact = (id: number, kind: FlowFactView['kind'], at: string, outcome: string | null = null): FlowFactView => ({
  id,
  flow_id: 'flw_1',
  chunk_id: 'ch_01flow00000000000000000000',
  submission_id: 'sub_a',
  kind,
  outcome,
  revision: null,
  at,
});

const DELIVERED: ChunkDetail = chunkDetail({
  chunk_id: 'ch_01flow00000000000000000000',
  status: 'done',
  current_node_id: 'nd_done',
  latest_epoch: 1,
  history: [
    transitionView({ from_node_id: 'nd_build', to_node_id: 'nd_deliver', choice_name: 'pass', epoch: 1, recorded_at: '2026-10-29T10:00:00Z' }),
  ],
  flow_record: [
    fact(1, 'submitted', '2026-10-29T10:01:00Z'),
    fact(2, 'turn-started', '2026-10-29T10:02:00Z'),
    fact(3, 'turn-ended', '2026-10-29T10:03:00Z', 'landed'),
    fact(4, 'landed', '2026-10-29T10:04:00Z'),
  ],
});

describe('deriveHistoryRows flow rows', () => {
  it('weaves the chunk\'s submission, turns and how they ended, and landing into the timeline by time', () => {
    const rows = deriveHistoryRows(DELIVERED, new Date('2026-10-29T12:00:00Z'));
    expect(rows.map((row) => row.kind)).toEqual(['transition', 'flow', 'flow', 'flow', 'flow']);
    const flows = rows.filter((row) => row.kind === 'flow');
    expect(flows.map((row) => row.nodeName)).toEqual(['submitted', 'turn started', 'turn ended', 'landed']);
    expect(flows[2].verdict).toBe('landed');
    expect(flows.every((row) => row.flowId === 'flw_1' && row.key === null)).toBe(true);
    expect(rows[0].flowId).toBeNull();
    expect(rowChoice(flows[0])).toBe('flow');
    expect(rowMark(flows[0])).not.toBe('');
  });

  it('reads a fact kind this build does not know raw, and a detail with no record as no flow rows', () => {
    const rows = deriveHistoryRows(
      chunkDetail({ flow_record: [fact(1, 'held' as FlowFactView['kind'], '2026-10-29T10:01:00Z')] }),
      new Date('2026-10-29T12:00:00Z'),
    );
    expect(rows.map((row) => row.nodeName)).toEqual(['held']);
    const runnerDetail = { ...chunkDetail(), flow_record: undefined } as unknown as ChunkDetail;
    expect(deriveHistoryRows(runnerDetail, new Date()).filter((row) => row.kind === 'flow')).toEqual([]);
  });
});

for (const [name, component, prefix] of [
  ['ChunkTimeline', ChunkTimeline, 'history'],
  ['ChunkTimelineSelection', ChunkTimelineSelection, 'selection'],
] as const) {
  describe(`${name} flow rows`, () => {
    beforeEach(async () => {
      await TestBed.configureTestingModule({
        imports: [component],
        providers: [provideZonelessChangeDetection(), provideRouter([{ path: 'board', children: [] }])],
      }).compileComponents();
    });

    async function render(flowLinkBase: readonly string[] | null): Promise<HTMLElement> {
      const fixture = TestBed.createComponent(component as Type<unknown>);
      fixture.componentRef.setInput('detail', DELIVERED);
      fixture.componentRef.setInput('flowLinkBase', flowLinkBase);
      await fixture.whenStable();
      return fixture.nativeElement as HTMLElement;
    }

    it('renders each flow fact as a row of its own, linked to its flow\'s record', async () => {
      const el = await render(['/board']);
      expect(el.querySelectorAll(`[data-testid="${prefix}-flow-step"]`)).toHaveLength(4);
      const links = [...el.querySelectorAll<HTMLAnchorElement>(`[data-testid="${prefix}-flow-link"]`)];
      expect(links).toHaveLength(4);
      expect(links[0].getAttribute('href')).toBe('/board?flow=flw_1');
      expect(links.map((link) => link.textContent?.trim())).toEqual(['submitted', 'turn started', 'turn ended', 'landed']);
    });

    it('withholds the link with no flow link base, still naming each fact', async () => {
      const el = await render(null);
      expect(el.querySelectorAll(`[data-testid="${prefix}-flow-link"]`)).toHaveLength(0);
      expect(el.querySelector(`[data-testid="${prefix}-flow-step"]`)?.textContent).toContain('submitted');
    });
  });
}

import type { FlowPlaceView, FlowView, WorkItemsView } from 'fleet';

import { deliveryLanes, laneTitle } from './delivery-band.model';

const NOW = Date.parse('2026-10-29T12:00:00Z');

const place = (chunk: string, arrival: number, over: Partial<FlowPlaceView> = {}): FlowPlaceView => ({
  chunk_id: chunk,
  submission_id: `sub_${chunk}`,
  arrival,
  state: 'waiting',
  position: arrival,
  next_poll_at: null,
  ...over,
});

const flow = (over: Partial<FlowView> = {}): FlowView => ({
  flow_id: 'flw_1',
  resource: 'repo_1:master',
  repository_id: 'repo_1',
  repository_name: 'blizzard',
  repository_slug: 'paul-gross/blizzard',
  branch: 'master',
  turn: null,
  line: [],
  last_verdict: null,
  last_landing: null,
  ...over,
});

const NO_ITEMS = new Map<string, WorkItemsView>();

describe('deliveryLanes', () => {
  it('titles a lane by its repository and branch', () => {
    expect(laneTitle(flow())).toBe('blizzard · master');
    expect(laneTitle(flow({ repository_name: null }))).toBe('paul-gross/blizzard · master');
  });

  it('leads with the chunk landing now, then the line in arrival order', () => {
    const [lane] = deliveryLanes(
      [
        flow({
          turn: { chunk_id: 'ch_a', started_at: '2026-10-29T11:59:00Z' },
          line: [place('ch_c', 3), place('ch_a', 1, { state: 'landing' }), place('ch_b', 2, { state: 'next' })],
        }),
      ],
      NO_ITEMS,
      NOW,
    );
    expect(lane.landing?.chunkId).toBe('ch_a');
    expect(lane.landing?.stateLabel).toBe('landing');
    expect(lane.line.map((chunk) => chunk.chunkId)).toEqual(['ch_b', 'ch_c']);
    expect(lane.collapsed).toBe(false);
  });

  it('has no landing chunk while no turn is open', () => {
    const [lane] = deliveryLanes([flow({ line: [place('ch_b', 2, { state: 'next' })] })], NO_ITEMS, NOW);
    expect(lane.landing).toBeNull();
    expect(lane.line.map((chunk) => chunk.stateLabel)).toEqual(['next']);
  });

  it('carries a polling chunk\'s next poll time, and no time for any other state', () => {
    const [lane] = deliveryLanes(
      [flow({ line: [place('ch_p', 1, { state: 'polling', next_poll_at: '2026-10-29T12:05:00Z' }), place('ch_w', 2)] })],
      NO_ITEMS,
      NOW,
    );
    expect(lane.line[0].pollAt).not.toBeNull();
    expect(lane.line[1].pollAt).toBeNull();
  });

  it('collapses a lane with nothing landing and no one in line to its last verdict and how long ago it came', () => {
    const [lane] = deliveryLanes(
      [
        flow({
          last_verdict: {
            id: 9, flow_id: 'flw_1', chunk_id: 'ch_a', submission_id: 'sub_a', kind: 'turn-ended',
            outcome: 'landed', revision: null, at: '2026-10-29T11:48:00Z',
          },
        }),
      ],
      NO_ITEMS,
      NOW,
    );
    expect(lane.collapsed).toBe(true);
    expect(lane.verdict).toEqual(expect.objectContaining({ outcome: 'landed', ago: '12m ago' }));
  });

  it('gives a flow the read omits no lane, and no active flow no lanes at all', () => {
    expect(deliveryLanes([flow({ flow_id: 'flw_1' }), flow({ flow_id: 'flw_2' })], NO_ITEMS, NOW)).toHaveLength(2);
    expect(deliveryLanes([flow({ flow_id: 'flw_2' })], NO_ITEMS, NOW).map((lane) => lane.flowId)).toEqual(['flw_2']);
    expect(deliveryLanes([], NO_ITEMS, NOW)).toEqual([]);
  });

  it('reads a state this build does not know raw, in the idle tone', () => {
    const [lane] = deliveryLanes([flow({ line: [place('ch_x', 1, { state: 'awaiting-merge' as FlowPlaceView['state'] })] })], NO_ITEMS, NOW);
    expect(lane.line[0]).toEqual(expect.objectContaining({ stateLabel: 'awaiting-merge', tone: 'idle' }));
  });

  it('titles a chunk from the board\'s work items once they are read, and leaves it to its short id until then', () => {
    const items = new Map<string, WorkItemsView>([['ch_a', { items: [{ title: 'Migrate the store' } as WorkItemsView['items'][number]] }]]);
    const [lane] = deliveryLanes([flow({ line: [place('ch_a', 1), place('ch_b', 2)] })], items, NOW);
    expect(lane.line.map((chunk) => chunk.title)).toEqual(['Migrate the store', null]);
    expect(lane.line[1].shortId).not.toBe('');
  });
});

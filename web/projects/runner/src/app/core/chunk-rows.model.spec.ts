import { runnerApi } from 'fleet';

import { BOARD_LANES, boardLane, machineChunkRows } from './chunk-rows.model';
import type { PanelLease, PanelProject } from './panel-reads.model';

const OWNER = { runnerId: 'rn_1', name: 'r1', label: 'r1' };

function lease(overrides: Partial<PanelLease> = {}): PanelLease {
  return {
    lease_id: 'lease_1',
    chunk_id: 'ch_1',
    graph_id: 'gr_1',
    node_id: 'nd_build',
    node_name: 'build',
    epoch: 1,
    session_id: 'sess-1',
    pid: 1,
    environment_id: 'e1',
    workdir: '/ws/e1',
    created_at: '2026-07-16T11:00:00.000Z',
    last_heartbeat_at: '2026-07-16T11:59:00.000Z',
    state: 'running',
    display_status: 'running',
    closure_healthy: false,
    closed_at: null,
    closure_reason: null,
    stale_after_seconds: 3600,
    resolved_model: null,
    resolved_effort: null,
    harness_version: null,
    git_commits: [],
    harness_id: null,
    project_id: null,
    owner: OWNER,
    projectSlug: null,
    ...overrides,
  };
}

describe('BOARD_LANES', () => {
  it('lays out needs human, in progress, then done', () => {
    expect(BOARD_LANES).toEqual([
      { key: 'needs', label: 'Needs human' },
      { key: 'progress', label: 'In progress' },
      { key: 'done', label: 'Done' },
    ]);
  });
});

describe('boardLane', () => {
  it('puts an escalation or an open ask in needs human', () => {
    expect(boardLane(lease({ state: 'closed', display_status: 'needs-human' }))).toBe('needs');
    expect(boardLane(lease({ state: 'parked', display_status: 'waiting-ask' }))).toBe('needs');
  });

  it('keeps a session a human holds in progress, even once its worker exited', () => {
    expect(boardLane(lease({ state: 'exited', display_status: 'human-in-session' }))).toBe('progress');
  });

  it('keeps a live worker — running, stale, or parked — in progress', () => {
    expect(boardLane(lease({ state: 'running' }))).toBe('progress');
    expect(boardLane(lease({ state: 'stale', display_status: 'stale' }))).toBe('progress');
    expect(boardLane(lease({ state: 'parked', display_status: 'parked' }))).toBe('progress');
  });

  it('puts an ended session — exited or closed — in done', () => {
    expect(boardLane(lease({ state: 'exited', display_status: 'exited' }))).toBe('done');
    expect(boardLane(lease({ state: 'closed', display_status: 'closed' }))).toBe('done');
  });
});

describe('machineChunkRows', () => {
  it('groups leases by chunk, newest lease first, attempts oldest → newest', () => {
    const a2 = lease({ lease_id: 'a2', chunk_id: 'ch_a', epoch: 2 });
    const b1 = lease({ lease_id: 'b1', chunk_id: 'ch_b', state: 'stale', display_status: 'stale' });
    const a1 = lease({ lease_id: 'a1', chunk_id: 'ch_a', state: 'closed', display_status: 'closed' });
    const rows = machineChunkRows([a2, b1, a1]);
    expect(rows.map((r) => r.lease)).toEqual([a2, b1]);
    expect(rows[0].leases).toEqual([a1, a2]);
    expect(rows[1].leases).toEqual([b1]);
    expect(rows.map((r) => r.status)).toEqual([
      { label: 'RUNNING', tone: 'running' },
      { label: 'STALE', tone: 'stale' },
    ]);
  });

  it("folds the newest lease's live worker and lane into the row", () => {
    const live = lease({ chunk_id: 'ch_live' });
    const ended = lease({ chunk_id: 'ch_done', state: 'closed', display_status: 'closed' });
    const [liveRow, endedRow] = machineChunkRows([live, ended]);
    expect(liveRow.live).toBe(live);
    expect(liveRow.lane).toBe('progress');
    expect(endedRow.live).toBeNull();
    expect(endedRow.lane).toBe('done');
  });

  it("reads each lease's wire display_status into its status", () => {
    const rows = machineChunkRows([
      lease({ chunk_id: 'ch_esc', display_status: 'needs-human' }),
      lease({ chunk_id: 'ch_tko', display_status: 'human-in-session' }),
      lease({ chunk_id: 'ch_ask', display_status: 'waiting-ask' }),
    ]);
    expect(rows.map((r) => r.status)).toEqual([
      { label: 'NEEDS HUMAN', tone: 'needs' },
      { label: 'HUMAN IN SESSION', tone: 'takeover' },
      { label: 'WAITING · ASK', tone: 'waiting' },
    ]);
  });

  it('maps no leases to no rows', () => {
    expect(machineChunkRows([])).toEqual([]);
  });

  it('reads a closed lease whose chunk transitioned as done', () => {
    const [only] = machineChunkRows([
      lease({
        state: 'closed',
        display_status: 'closed',
        closure_reason: runnerApi.LeaseClosureReason.TRANSITIONED,
        closure_healthy: true,
      }),
    ]);
    expect(only.status.tone).toBe('done');
  });

  describe('project attribution', () => {
    const projects: PanelProject[] = [
      { slug: 'blizzard', name: 'Blizzard', entries: ['blizzard'], color: 'cyan' },
      { slug: 'ops', name: 'Ops', entries: ['ops'], color: null },
    ];

    it("names and tints a row by its served project's name and colour", () => {
      const [row] = machineChunkRows([lease({ projectSlug: 'blizzard' })], projects);
      expect(row.projectName).toBe('Blizzard');
      expect(row.projectColor).toBe('cyan');
    });

    it('leaves a project with no colour untinted', () => {
      const [row] = machineChunkRows([lease({ projectSlug: 'ops' })], projects);
      expect(row.projectName).toBe('Ops');
      expect(row.projectColor).toBeNull();
    });

    it('names a project no served project accounts for by its slug, untinted', () => {
      const [row] = machineChunkRows([lease({ projectSlug: 'stray' })], projects);
      expect(row.projectName).toBe('stray');
      expect(row.projectColor).toBeNull();
    });

    it('names no project where none can be told', () => {
      const [row] = machineChunkRows([lease({ projectSlug: null })], projects);
      expect(row.projectName).toBeNull();
      expect(row.projectColor).toBeNull();
    });
  });
});

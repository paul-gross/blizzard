import type { LoggedEvent } from 'fleet';
import { summarizeChunkChange } from './chunk-change-summary';

/** No runner's host known — every runner keeps its compact-id name. */
const NO_HOSTS: ReadonlyMap<string, string> = new Map();

type ChunkChangedData = LoggedEvent['data'];

describe('summarizeChunkChange', () => {
  it('renders the full transition and runner lines from a fully-populated frame', () => {
    const data: ChunkChangedData = {
      chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN1RJ1',
      status: 'failed',
      prev_status: 'running',
      prev_node: 'review',
      node: 'build',
      runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3',
      runner_name: 'r-claude',
      cause: 'node-completed',
      graph_id: 'gr_01KXKVVF1J3D6H6VYZ3XYN0001',
    };
    const summary = summarizeChunkChange(data, NO_HOSTS);
    expect(summary.transition).toBe('C-1RJ1 review → failed → build');
    expect(summary.runner).toBe('R-ABF3.r-claude');
  });

  it('names the claiming runner by display name, a runner with no name by its compact id', () => {
    const claimed: ChunkChangedData = { chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN1RJ1', cause: 'claimed', status: 'running' };
    expect(
      summarizeChunkChange({ ...claimed, runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3', runner_name: 'r-claude' }, NO_HOSTS),
    ).toEqual({ transition: 'C-1RJ1 claimed', runner: 'R-ABF3.r-claude' });
    expect(summarizeChunkChange({ ...claimed, runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYN7Q2M' }, NO_HOSTS).runner).toBe('R-7Q2M');
  });

  it('names the runner host/name where its host is known', () => {
    const claimed: ChunkChangedData = { chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN1RJ1', cause: 'claimed', status: 'running' };
    const hosts = new Map([['rn_01KXKVVF1J3D6H6VYZ3XYNABF3', 'atlas']]);
    expect(
      summarizeChunkChange({ ...claimed, runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3', runner_name: 'r-claude' }, hosts).runner,
    ).toBe('atlas/r-claude');
    const nameless: ChunkChangedData = { ...claimed, cause: 'node-completed', runner_id: 'rn_01KXKVVF1J3D6H6VYZ3XYNABF3' };
    expect(summarizeChunkChange(nameless, hosts).runner).toBe('atlas/R-ABF3');
  });

  it('omits the runner line entirely when the frame names no runner', () => {
    const data: ChunkChangedData = { chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN1RJ1', status: 'ready' };
    expect(summarizeChunkChange(data, NO_HOSTS).runner).toBeUndefined();
  });

  it('degrades a frame with neither node to a one-line transition', () => {
    const data: ChunkChangedData = { chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9', status: 'running' };
    expect(summarizeChunkChange(data, NO_HOSTS).transition).toBe('C-3YJ9 → running');
  });

  it('drops only the missing segment when one node is absent', () => {
    // No prev_node (e.g. a fresh promote — the chunk has never transitioned).
    const noPrev: ChunkChangedData = { chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9', status: 'ready', node: 'build' };
    expect(summarizeChunkChange(noPrev, NO_HOSTS).transition).toBe('C-3YJ9 → ready → build');
    // No node (e.g. the chunk just landed the terminal transition).
    const noNext: ChunkChangedData = {
      chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
      status: 'done',
      prev_node: 'deliver',
    };
    expect(summarizeChunkChange(noNext, NO_HOSTS).transition).toBe('C-3YJ9 deliver → done');
  });

  // --- Delete's actor -------------------------------------

  it('falls back to the deleting actor when a deleted-cause frame carries no runner_id', () => {
    const data: ChunkChangedData = {
      chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
      status: 'not_ready',
      cause: 'deleted',
      by: 'operator',
    };
    const summary = summarizeChunkChange(data, NO_HOSTS);
    expect(summary.transition).toBe('C-3YJ9 → not_ready');
    expect(summary.runner).toBe('operator');
  });

  it('prefers runner_id over by when a frame somehow carries both', () => {
    const data: ChunkChangedData = {
      chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
      status: 'running',
      cause: 'deleted',
      runner_id: 'runner-local',
      by: 'operator',
    };
    expect(summarizeChunkChange(data, NO_HOSTS).runner).toBe('runner-local');
  });

  it('does not surface by for a non-deleted cause even when the frame carries one', () => {
    const data: ChunkChangedData = {
      chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
      status: 'running',
      cause: 'node-completed',
      by: 'operator',
    };
    expect(summarizeChunkChange(data, NO_HOSTS).runner).toBeUndefined();
  });

  it('never surfaces graph_id in either rendered line', () => {
    const data: ChunkChangedData = {
      chunk_id: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
      status: 'running',
      runner_id: 'runner-local',
      graph_id: 'gr_01KXKVVF1J3D6H6VYZ3XYN0001',
    };
    const summary = summarizeChunkChange(data, NO_HOSTS);
    expect(summary.transition).not.toContain('gr_');
    expect(summary.runner).not.toContain('gr_');
  });
});

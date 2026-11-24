import { describe, expect, it } from 'vitest';
import { chunkSummary } from 'fleet/testing';
import { toRunnerClaimChunk } from './runner-claims.translate';

describe('runner claim chunk translation', () => {
  it('carries who holds the chunk, where it sits and what it holds', () => {
    const chunk = toRunnerClaimChunk(chunkSummary({
      chunk_id: 'c1', graph_id: 'g', runner_id: 'r1', current_node_id: 'n', current_node_name: 'Build', status: 'running',
      environment_count: 2, workspace_id: 'ws', project_id: 'p', project_slug: 'proj',
    }));
    expect(chunk).toEqual({
      chunk_id: 'c1', runner_id: 'r1', current_node_id: 'n', current_node_name: 'Build', laneStatus: { kind: 'running' },
      environment_count: 2, workspace_id: 'ws', project_id: 'p', project_slug: 'proj',
    });
  });

  it('maps a status it does not know onto the unknown lane status', () => {
    expect(toRunnerClaimChunk(chunkSummary({ chunk_id: 'c1', graph_id: 'g', status: 'orbiting' as never })).laneStatus)
      .toEqual({ kind: 'unknown', raw: 'orbiting' });
  });
});

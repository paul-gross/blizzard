import { describe, expect, it } from 'vitest';
import { hostEnvironmentView } from 'fleet/testing';

import { fromHostWorkspaceEnvironmentsView } from './host-environment.translate';

describe('fromHostWorkspaceEnvironmentsView', () => {
  it("carries each environment's hold, holder, chunk and project", () => {
    expect(
      fromHostWorkspaceEnvironmentsView({
        workspace_id: 'ws',
        items: [
          hostEnvironmentView({ environment_id: 'e1', held: true, chunk_id: 'ch_1', runner_id: 'rn_1', project: 'blizzard' }),
          hostEnvironmentView({ environment_id: 'e2' }),
        ],
      }),
    ).toEqual({
      workspace_id: 'ws',
      items: [
        { environment_id: 'e1', held: true, chunk_id: 'ch_1', project: 'blizzard', runner_id: 'rn_1' },
        { environment_id: 'e2', held: false, chunk_id: null, project: null, runner_id: null },
      ],
    });
  });

  it('reads an empty workspace as one with no environments', () => {
    expect(fromHostWorkspaceEnvironmentsView({ workspace_id: 'ws', items: [] })).toEqual({ workspace_id: 'ws', items: [] });
  });
});

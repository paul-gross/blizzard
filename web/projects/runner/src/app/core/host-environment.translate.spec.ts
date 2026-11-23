import { describe, expect, it } from 'vitest';
import { hostEnvironmentView } from 'fleet/testing';

import { fromHostWorkspaceEnvironmentsView } from './host-environment.translate';

describe('fromHostWorkspaceEnvironmentsView', () => {
  it("carries each environment's hold and holder", () => {
    expect(
      fromHostWorkspaceEnvironmentsView({
        workspace_id: 'ws',
        items: [
          hostEnvironmentView({ environment_id: 'e1', held: true, chunk_id: 'ch_1', runner_id: 'rn_1' }),
          hostEnvironmentView({ environment_id: 'e2' }),
        ],
      }),
    ).toEqual({ items: [{ held: true, runner_id: 'rn_1' }, { held: false, runner_id: null }] });
  });

  it('reads an empty workspace as one with no environments', () => {
    expect(fromHostWorkspaceEnvironmentsView({ workspace_id: 'ws', items: [] })).toEqual({ items: [] });
  });
});

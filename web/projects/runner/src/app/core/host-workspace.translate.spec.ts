import { describe, expect, it } from 'vitest';

import { fromHostWorkspaceView } from './host-workspace.translate';

describe('fromHostWorkspaceView', () => {
  it('carries the workspace and its environment ceiling', () => {
    expect(
      fromHostWorkspaceView({ workspace_id: 'web', max_environments: 3, revision: 2, settings: { a: 1 }, state: 'active' }),
    ).toEqual({ workspace_id: 'web', max_environments: 3 });
  });
});

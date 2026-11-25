import { describe, expect, it } from 'vitest';

import { fromHostWorkspaceView } from './host-workspace.translate';

describe('fromHostWorkspaceView', () => {
  it('carries the workspace, its environment ceiling, and its record`s revision, state, and settings', () => {
    const settings = { root: '/srv/web', max_environments: 3 };
    const workspace = fromHostWorkspaceView({ workspace_id: 'web', max_environments: 3, revision: 2, settings, state: 'retired' });
    expect(workspace).toEqual({ workspace_id: 'web', max_environments: 3, revision: 2, state: 'retired', settings });
    expect(workspace.settings).not.toBe(settings);
  });

  it('keeps an unknown state raw, and reads a host keeping no records as revision null', () => {
    const wire = { workspace_id: 'web', max_environments: 1, revision: null, settings: {}, state: 'paused' } as never;
    expect(fromHostWorkspaceView(wire)).toMatchObject({ revision: null, state: { kind: 'unknown', raw: 'paused' } });
  });
});

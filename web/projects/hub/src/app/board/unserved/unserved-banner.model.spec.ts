import type { ProjectView, RunnerRegistryView } from 'fleet';

import { unservedBannerText } from './unserved-banner.model';

const GLACIER = { project_id: 'proj_g', slug: 'glacier', name: 'Glacier' } as ProjectView;

const runner = (over: Partial<RunnerRegistryView>): RunnerRegistryView =>
  ({
    runner_id: 'rn_1',
    online: true,
    retired: false,
    projects: [{ entry: 'glacier', project_id: 'proj_g', slug: 'glacier', name: 'Glacier' }],
    ...over,
  }) as RunnerRegistryView;

describe('unservedBannerText', () => {
  it('names a lensed project that no live runner serves', () => {
    expect(unservedBannerText(GLACIER, [])).toBe(
      'No live runner serves Glacier: its ready work waits in the queue until one does.',
    );
    expect(unservedBannerText(GLACIER, [runner({ online: false })])).not.toBeNull();
    expect(unservedBannerText(GLACIER, [runner({ retired: true })])).not.toBeNull();
    expect(unservedBannerText(GLACIER, [runner({ projects: [] })])).not.toBeNull();
  });

  it('shows nothing while a live runner serves the project', () => {
    expect(unservedBannerText(GLACIER, [runner({})])).toBeNull();
  });

  it('shows nothing on All, or before the project or the runners are read', () => {
    expect(unservedBannerText(null, [])).toBeNull();
    expect(unservedBannerText(GLACIER, undefined)).toBeNull();
  });
});

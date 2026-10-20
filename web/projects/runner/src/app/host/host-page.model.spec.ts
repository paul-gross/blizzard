import type { HostEnvGroup } from './host-environments.model';
import { envsInUseLabel, hostRefLabel, hostRowsInFilter, hostRunnersEmptyText, hostTone } from './host-page.model';
import type { HostRegistrationSummary } from './host-registration.model';
import type { HostRunnerRow } from './host-runner-row.model';
import type { PanelProject, RunnerProjects } from '../core/panel-reads.model';

const REG = (state: HostRegistrationSummary['state']): HostRegistrationSummary => ({
  state,
  label: state,
  tone: 'idle',
  lines: [],
  answeredAt: null,
});

const ROW = (name: string, runnerId: string | null): HostRunnerRow => ({ name, runnerId, key: runnerId ?? name }) as HostRunnerRow;

const PROJECT = (slug: string): PanelProject => ({ slug, name: slug, entries: [slug], color: null });

const GROUP = (capacity: number | null, inUse: number, envs: number): HostEnvGroup => ({
  workspaceId: 'ws',
  capacity,
  inUse,
  envs: Array.from({ length: envs }, (_, i) => ({
    environmentId: `e${i}`,
    held: false,
    chunkRef: null,
    chunkId: null,
    project: null,
    holder: null,
    elsewhere: false,
    projectName: null,
    color: null,
  })),
});

describe('hostRefLabel', () => {
  it('is null before the host registers under an id', () => {
    expect(hostRefLabel(null)).toBeNull();
  });

  it('shortens the host id to its compact ref', () => {
    const ref = hostRefLabel('hs_01KXKVVF1J3D6H6VYZ3XYN3YJ9');
    expect(ref).not.toBeNull();
    expect(ref!.length).toBeLessThan('hs_01KXKVVF1J3D6H6VYZ3XYN3YJ9'.length);
  });
});

describe('hostTone', () => {
  it('is green once registered, red refused or unreachable, amber otherwise', () => {
    expect(hostTone(REG('registered'))).toBe('green');
    expect(hostTone(REG('refused'))).toBe('red');
    expect(hostTone(REG('unreachable'))).toBe('red');
    expect(hostTone(REG('pending'))).toBe('amber');
    expect(hostTone(REG('unregistered'))).toBe('amber');
    expect(hostTone(REG('no_hosts'))).toBe('amber');
    expect(hostTone(null)).toBe('amber');
  });
});

describe('hostRowsInFilter', () => {
  const rows = [ROW('r1', 'rn_1'), ROW('r2', 'rn_2'), ROW('halted', null)];
  const byRunner: RunnerProjects = new Map([
    ['rn_1', [PROJECT('console')]],
    ['rn_2', [PROJECT('ops')]],
  ]);

  it('leaves every row with both filters All', () => {
    expect(hostRowsInFilter(rows, null, null, byRunner).map((row) => row.name)).toEqual(['r1', 'r2', 'halted']);
  });

  it('leaves the runner the Runner filter names', () => {
    expect(hostRowsInFilter(rows, 'r2', null, byRunner).map((row) => row.name)).toEqual(['r2']);
  });

  it('leaves the runners serving the Project filter’s project, dropping the id-less runner', () => {
    expect(hostRowsInFilter(rows, null, 'console', byRunner).map((row) => row.name)).toEqual(['r1']);
  });

  it('leaves nothing when the two filters disagree', () => {
    expect(hostRowsInFilter(rows, 'r2', 'console', byRunner)).toEqual([]);
  });
});

describe('envsInUseLabel', () => {
  it('is null while no workspace is reported', () => {
    expect(envsInUseLabel([])).toBeNull();
  });

  it('sums in-use over capacity, counting an undeclared workspace’s environments', () => {
    expect(envsInUseLabel([GROUP(3, 2, 3), GROUP(null, 1, 2)])).toBe('3 / 5 in use');
  });
});

describe('hostRunnersEmptyText', () => {
  it('names a host with no runners', () => {
    expect(hostRunnersEmptyText(0)).toBe('NO RUNNERS ON THIS HOST');
  });

  it('names the filters when the host has runners they leave out', () => {
    expect(hostRunnersEmptyText(2)).toBe('NO RUNNER MATCHES THE FILTERS');
  });
});

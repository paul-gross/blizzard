import type { runnerApi } from 'fleet';

import type { RunnerDashboard } from '../../core/panel-reads.model';
import { capacitiesByRunner, hostPanelRunners, runnersNamed } from './host-panel.model';

function runner(overrides: Partial<runnerApi.HostRunnerView> = {}): runnerApi.HostRunnerView {
  return {
    runner_id: 'rn_1',
    name: 'r1',
    state: 'serving',
    served: true,
    halted_reasons: [],
    pause: { local: false, hub: false, effective: false, local_reason: null },
    bindings: [{ project: 'p', workspace_id: 'ws', agents: 4 }],
    agents_in_use: 1,
    max_agents: 4,
    ...overrides,
  };
}

function capacities(bindings: { project: string; workspace_id: string; used: number }[]): runnerApi.CapacitiesView {
  return {
    used: bindings.reduce((sum, binding) => sum + binding.used, 0),
    agents: 4,
    free: 4 - bindings.reduce((sum, binding) => sum + binding.used, 0),
    bindings: bindings.map((binding) => ({ ...binding, project_id: `proj_${binding.project}`, agents: 4, free: 4 - binding.used })),
  };
}

const NO_CAPACITIES = new Map<string, runnerApi.CapacitiesView>();

describe('hostPanelRunners', () => {
  describe('light and brakes', () => {
    it('lights a serving runner with neither brake on green', () => {
      const [only] = hostPanelRunners([runner()], NO_CAPACITIES);
      expect(only.tone).toBe('green');
      expect(only.localPause).toBeNull();
      expect(only.hubPaused).toBe(false);
      expect(only.halted).toBe(false);
    });

    it('lights a runner its own brake holds amber, worded with its reason', () => {
      const [only] = hostPanelRunners(
        [runner({ pause: { local: true, hub: false, effective: true, local_reason: 'OpenCode is not installed' } })],
        NO_CAPACITIES,
      );
      expect(only.tone).toBe('amber');
      expect(only.localPause).toBe('Paused here: OpenCode is not installed');
    });

    it('words its own brake without a reason as no new claims', () => {
      const [only] = hostPanelRunners([runner({ pause: { local: true, hub: false, effective: true, local_reason: null } })], NO_CAPACITIES);
      expect(only.localPause).toBe('Paused here — no new claims');
    });

    it("lights a runner the hub's brake holds amber, its own brake off", () => {
      const [only] = hostPanelRunners([runner({ pause: { local: false, hub: true, effective: true, local_reason: null } })], NO_CAPACITIES);
      expect(only.tone).toBe('amber');
      expect(only.hubPaused).toBe(true);
      expect(only.localPause).toBeNull();
    });

    it('lights a halted runner red with its reasons, its brakes and bindings dropped', () => {
      const [only] = hostPanelRunners(
        [
          runner({
            runner_id: null,
            state: 'halted',
            halted_reasons: ['the hub refuses its token'],
            pause: { local: true, hub: true, effective: true, local_reason: 'x' },
          }),
        ],
        NO_CAPACITIES,
      );
      expect(only.tone).toBe('red');
      expect(only.halted).toBe(true);
      expect(only.haltedReasons).toEqual(['the hub refuses its token']);
      expect(only.localPause).toBeNull();
      expect(only.hubPaused).toBe(false);
      expect(only.bindings).toEqual([]);
    });

    it('keeps no halted reasons for a serving runner', () => {
      const [only] = hostPanelRunners([runner({ halted_reasons: ['stale reason'] })], NO_CAPACITIES);
      expect(only.haltedReasons).toEqual([]);
    });
  });

  it('keys each runner by its id, a runner with none by its name and place, in wire order', () => {
    const rows = hostPanelRunners([runner({ runner_id: 'rn_a', name: 'a' }), runner({ runner_id: null, name: 'b', state: 'halted' })], NO_CAPACITIES);
    expect(rows.map((row) => row.key)).toEqual(['rn_a', 'b#1']);
    expect(rows.map((row) => row.name)).toEqual(['a', 'b']);
  });

  it("carries the runner's total agents for one that declares no binding", () => {
    const [only] = hostPanelRunners([runner({ bindings: [], agents_in_use: 3, max_agents: 6 })], NO_CAPACITIES);
    expect(only.bindings).toEqual([]);
    expect(only.agentsInUse).toBe(3);
    expect(only.maxAgents).toBe(6);
  });

  describe('binding lines', () => {
    it('labels a binding by its project alone while the runner binds one workspace', () => {
      const [only] = hostPanelRunners(
        [
          runner({
            bindings: [
              { project: 'blizzard', workspace_id: 'workspace-local', agents: 8 },
              { project: 'winter', workspace_id: 'workspace-local', agents: 2 },
            ],
          }),
        ],
        NO_CAPACITIES,
      );
      expect(only.bindings.map((binding) => binding.label)).toEqual(['blizzard', 'winter']);
    });

    it('labels each binding with its workspace beside the project once the runner binds more than one', () => {
      const [only] = hostPanelRunners(
        [
          runner({
            bindings: [
              { project: 'console', workspace_id: 'workspace-web', agents: 4 },
              { project: 'ops', workspace_id: 'workspace-local', agents: 2 },
            ],
          }),
        ],
        NO_CAPACITIES,
      );
      expect(only.bindings.map((binding) => binding.label)).toEqual(['console · workspace-web', 'ops · workspace-local']);
      expect(only.bindings.map((binding) => binding.key)).toEqual(['console@workspace-web', 'ops@workspace-local']);
    });

    it("reads each binding's agents in use off the runner's own capacities, matched by project and workspace", () => {
      const [only] = hostPanelRunners(
        [
          runner({
            bindings: [
              { project: 'console', workspace_id: 'workspace-web', agents: 4 },
              { project: 'console', workspace_id: 'workspace-local', agents: 2 },
            ],
          }),
        ],
        new Map([
          [
            'rn_1',
            capacities([
              { project: 'console', workspace_id: 'workspace-local', used: 1 },
              { project: 'console', workspace_id: 'workspace-web', used: 3 },
            ]),
          ],
        ]),
      );
      expect(only.bindings.map(({ used, agents }) => ({ used, agents }))).toEqual([
        { used: 3, agents: 4 },
        { used: 1, agents: 2 },
      ]);
    });

    it('reads 0 in use before the capacities land, or for a binding they do not name', () => {
      const bindings = [
        { project: 'p', workspace_id: 'ws', agents: 4 },
        { project: 'q', workspace_id: 'ws', agents: 2 },
      ];
      const [before] = hostPanelRunners([runner({ bindings })], NO_CAPACITIES);
      expect(before.bindings.map((binding) => binding.used)).toEqual([0, 0]);

      const [after] = hostPanelRunners([runner({ bindings })], new Map([['rn_1', capacities([{ project: 'p', workspace_id: 'ws', used: 2 }])]]));
      expect(after.bindings.map((binding) => binding.used)).toEqual([2, 0]);
    });

    it("never reads another runner's capacities", () => {
      const [only] = hostPanelRunners([runner()], new Map([['rn_2', capacities([{ project: 'p', workspace_id: 'ws', used: 4 }])]]));
      expect(only.bindings[0].used).toBe(0);
    });
  });
});

describe('runnersNamed', () => {
  const runners = [runner({ name: 'r1' }), runner({ runner_id: 'rn_2', name: 'r2' })];

  it('keeps every runner for All', () => {
    expect(runnersNamed(runners, null)).toBe(runners);
  });

  it('narrows to the runner the filter names', () => {
    expect(runnersNamed(runners, 'r2')).toEqual([runners[1]]);
  });

  it('keeps none for a name no runner has', () => {
    expect(runnersNamed(runners, 'gone')).toEqual([]);
  });
});

describe('capacitiesByRunner', () => {
  it("keys each runner's own slot accounting by its id", () => {
    const first = capacities([{ project: 'p', workspace_id: 'ws', used: 1 }]);
    const second = capacities([]);
    const dashboard = (runnerId: string, caps: runnerApi.CapacitiesView): RunnerDashboard => ({
      owner: { runnerId, name: runnerId, label: runnerId },
      dashboard: { runner: { capacities: caps } } as runnerApi.DashboardView,
    });

    const byRunner = capacitiesByRunner([dashboard('rn_1', first), dashboard('rn_2', second)]);
    expect([...byRunner.entries()]).toEqual([
      ['rn_1', first],
      ['rn_2', second],
    ]);
  });
});

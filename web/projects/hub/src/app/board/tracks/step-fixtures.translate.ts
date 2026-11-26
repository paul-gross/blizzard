import type { hubApi } from 'fleet/hub-api';

export const BUILD: hubApi.EnvTimelinePlacedStepView = {
  environment_id: 'env_a',
  host_id: 'host_01',
  host_name: 'xps',
  workspace_id: 'ws_1',
  segment: {
    chunk_id: 'ch_01',
    cost_estimated: false,
    cost_usd: 0,
    ended_at: '2026-07-13T10:30:00Z',
    epoch: 1,
    escalation: null,
    escalation_open: false,
    executor: 'runner',
    exit_choice: 'pass',
    failed: false,
    graph_id: 'gr_1',
    graph_name: 'delivery',
    lease_id: 'lease_01',
    node_id: 'nd_build',
    node_name: 'build',
    project_id: null,
    runner_id: 'rn_01',
    runner_name: 'runner-one',
    started_at: '2026-07-13T10:00:00Z',
    title: null,
  },
};

export const VIEW: hubApi.EnvTimelineStepView = {
  artifact_keys: [],
  escalations: [],
  generated_at: '2026-07-13T12:00:00Z',
  invocations: [],
  spend: null,
  journey: [BUILD],
  step: BUILD,
};

export function unlistedStep(epoch: number | null): hubApi.EnvTimelineStepView {
  const step = { ...BUILD, segment: { ...BUILD.segment, executor: 'cloud' as hubApi.Executor, epoch } };
  return { ...VIEW, journey: [step], step };
}

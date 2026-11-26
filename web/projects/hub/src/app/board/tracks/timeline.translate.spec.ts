import type { hubApi } from 'fleet/hub-api';
import { expandTimeline, fromStepExecutor, fromInvocationKind, fromTimelineStep } from './timeline.translate';

const at = (hhmm: string): string => `2026-07-01T${hhmm}:00Z`;

describe('expandTimeline', () => {
  const block = (overrides: Partial<hubApi.EnvTimelineBlockView> = {}): hubApi.EnvTimelineBlockView => ({
    chunk_id: 'ch_AAAA0001',
    cost_estimated: false,
    cost_usd: 1,
    ended_at: at('10:10'),
    epoch: 2,
    escalation: null,
    escalation_open: false,
    executor: 'runner',
    exit_choice: 'pass',
    failed: false,
    lease_id: 'lease_1',
    node_id: 'nd_build',
    runner_id: 'rn_1',
    started_at: at('10:00'),
    ...overrides,
  });

  const wire = (blocks: hubApi.EnvTimelineBlockView[]): hubApi.EnvTimelineView => ({
    since: at('09:00'),
    until: at('12:00'),
    generated_at: at('11:00'),
    chunks: { ch_AAAA0001: { title: 'Fix it', project_id: 'proj_1' } },
    nodes: { nd_build: { graph_name: 'delivery', node_name: 'build' } },
    runners: { rn_1: 'alpha' },
    hosts: [
      {
        host_id: 'hs_1',
        host_name: 'xps',
        runner_names: ['alpha'],
        workspaces: [{ workspace_id: 'ws1', environments: [{ environment_id: 'e1', blocks }] }],
      },
    ],
  });

  it('lays the chunk, node and runner names back onto each block', () => {
    const out = expandTimeline(wire([block()]));
    expect(out.hosts[0].workspaces[0].environments[0].segments).toEqual([
      { ...block(), executor: { kind: 'runner' }, title: 'Fix it', project_id: 'proj_1', runner_name: 'alpha', graph_id: null, graph_name: 'delivery', node_name: 'build' },
    ]);
  });

  it('keeps the window, the host and the environment ids', () => {
    const out = expandTimeline(wire([block()]));
    expect(out).toMatchObject({ since: at('09:00'), until: at('12:00'), generated_at: at('11:00') });
    expect(out.hosts[0]).toMatchObject({ host_id: 'hs_1', host_name: 'xps', runner_names: ['alpha'] });
    expect(out.hosts[0].workspaces[0].workspace_id).toBe('ws1');
    expect(out.hosts[0].workspaces[0].environments[0].environment_id).toBe('e1');
  });

  it('falls back to the ids when a table lacks them', () => {
    const [segment] = expandTimeline(wire([block({ chunk_id: 'ch_ZZZZ', node_id: 'nd_missing', runner_id: 'rn_missing' })])).hosts[0]
      .workspaces[0].environments[0].segments;
    expect(segment).toMatchObject({
      title: null,
      project_id: null,
      graph_name: null,
      node_name: 'nd_missing',
      runner_name: 'rn_missing',
    });
  });
});


describe('open timeline vocabularies', () => {
  it('translates every known executor and invocation value', () => {
    expect(fromStepExecutor(null)).toBeNull();
    expect(fromStepExecutor('runner')).toEqual({ kind: 'runner' });
    expect(fromStepExecutor('hub')).toEqual({ kind: 'hub' });
    expect(fromInvocationKind('spawn')).toEqual({ kind: 'spawn' });
    expect(fromInvocationKind('resume')).toEqual({ kind: 'resume' });
    expect(fromInvocationKind('judge')).toEqual({ kind: 'judge' });
  });

  it('preserves unknown executor and invocation values', () => {
    expect(fromStepExecutor('future' as hubApi.Executor)).toEqual({ kind: 'unknown', raw: 'future' });
    expect(fromInvocationKind('future' as hubApi.UsageKind)).toEqual({ kind: 'unknown', raw: 'future' });
  });
});

describe('step translation', () => {
  it('translates executor and invocation vocabularies throughout the step and journey', () => {
    const segment: hubApi.EnvTimelineSegmentView = {
      chunk_id: 'ch_1', cost_estimated: false, cost_usd: 1, ended_at: null, epoch: 2,
      escalation: null, escalation_open: false, executor: 'future' as hubApi.Executor, exit_choice: null,
      failed: false, graph_id: null, graph_name: null, lease_id: 'l1', node_id: 'nd_1',
      node_name: 'build', project_id: null, runner_id: 'r1', runner_name: 'runner',
      started_at: at('10:00'), title: null,
    };
    const placed = { environment_id: 'e1', host_id: null, host_name: null, workspace_id: 'ws1', segment };
    const wire: hubApi.EnvTimelineStepView = {
      artifact_keys: ['a'], escalations: [], generated_at: at('11:00'), journey: [placed], step: placed, spend: null,
      invocations: [{
        kind: 'new-kind' as hubApi.UsageKind, cache_create_tokens: 0, cache_read_tokens: 0, cost_usd: null,
        estimated_cost_usd: null, harness_id: null, input_tokens: 0, model: null, output_tokens: 0, recorded_at: at('10:00'),
      }],
    };
    expect(fromTimelineStep(wire)).toMatchObject({
      step: { segment: { executor: { kind: 'unknown', raw: 'future' } } },
      journey: [{ segment: { executor: { kind: 'unknown', raw: 'future' } } }],
      invocations: [{ kind: { kind: 'unknown', raw: 'new-kind' } }],
      artifact_keys: ['a'],
    });
  });
});

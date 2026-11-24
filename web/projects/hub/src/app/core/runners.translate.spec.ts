import { describe, expect, it } from 'vitest';
import { runnerRegistryView } from 'fleet/testing';
import { servingProjectRunners } from './runners.domain';
import { fromGlanceConnection, toGlanceRunner, toHubRunner, toProjectRunner } from './runners.translate';

describe('project runner projection', () => {
  it('uses online and the hub claimability judgment, not paused flags', () => {
    const runner = toProjectRunner(runnerRegistryView({ runner_id: 'r', runner_name: 'Runner', connection: 'online', online: true,
      hub_paused: true, projects: [{ entry: 'p', project_id: 'p', claimable: true, name: 'Project', slug: 'project', color: 'teal' }] }));
    expect(servingProjectRunners('p', [runner])).toEqual([runner]);
    expect(servingProjectRunners('other', [runner])).toEqual([]);
    expect(servingProjectRunners('p', [{ ...runner, online: false }])).toEqual([]);
    expect(servingProjectRunners('p', [{ ...runner, projects: [{ project_id: 'p', claimable: false }] }])).toEqual([]);
  });
});

describe('hub runner translation', () => {
  const wire = runnerRegistryView;

  it('carries the hub judgements from their fields', () => {
    const runner = toHubRunner(wire({
      runner_id: 'r', online: false, connection: 'offline', version_standing: 'refused', version_refusal: 'too-old',
      projects: [{ entry: 'p', project_id: 'p', claimable: false, name: null, slug: null, color: null }],
      declining: [{ lever: 'floor:s', lever_kind: 'floor', detail: 'at 90%', subscription: 's', until: null }],
    }));
    expect(runner.online).toBe(false);
    expect(runner.connection).toBe('offline');
    expect(runner.version_standing).toBe('refused');
    expect(runner.version_refusal).toBe('too-old');
    expect(runner.projects[0].claimable).toBe(false);
    expect(runner.declining).toEqual([{ lever: 'floor:s', lever_kind: 'floor', detail: 'at 90%', subscription: 's', until: null }]);
  });

  it('maps every vocabulary value it does not know onto an explicit unknown', () => {
    const runner = toHubRunner(wire({
      runner_id: 'r',
      connection: 'dormant' as never,
      version_standing: 'probation' as never,
      version_refusal: 'too-odd' as never,
      declining: [{ lever: 'x', lever_kind: 'moon' as never, detail: 'd', subscription: null, until: null }],
      unenforceable_gates: [{ node: 'deliver', reason: 'sideways' as never }],
    }));
    expect(runner.connection).toEqual({ kind: 'unknown', raw: 'dormant' });
    expect(runner.version_standing).toEqual({ kind: 'unknown', raw: 'probation' });
    expect(runner.version_refusal).toEqual({ kind: 'unknown', raw: 'too-odd' });
    expect(runner.declining[0].lever_kind).toEqual({ kind: 'unknown', raw: 'moon' });
    expect(runner.unenforceable_gates).toEqual([{ node: 'deliver', reason: { kind: 'unknown', raw: 'sideways' } }]);
  });

  it('keeps a known gate reason, standing and lever as themselves', () => {
    const runner = toHubRunner(wire({
      runner_id: 'r', version_standing: 'in_window',
      declining: [{ lever: 'pace', lever_kind: 'pace', detail: 'd', subscription: null, until: null }],
      unenforceable_gates: [{ node: 'deliver', reason: 'hub-executed' }, { node: 'x', reason: 'unknown-node' }],
    }));
    expect(runner.version_standing).toBe('in_window');
    expect(runner.version_refusal).toBeNull();
    expect(runner.declining[0].lever_kind).toBe('pace');
    expect(runner.unenforceable_gates.map((gate) => gate.reason)).toEqual(['hub-executed', 'unknown-node']);
  });
});

describe('glance runner projection', () => {
  it('keeps hub liveness judgments and preserves unknown connection values', () => {
    expect(toGlanceRunner(runnerRegistryView({ runner_id: 'r', online: true, retired: false, connection: 'online' })))
      .toEqual({ runner_id: 'r', online: true, retired: false, connection: { kind: 'online' } });
    expect(fromGlanceConnection('new-connection')).toEqual({ kind: 'unknown', raw: 'new-connection' });

  });
});

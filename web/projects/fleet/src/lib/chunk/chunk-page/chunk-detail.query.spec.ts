import { assetArtifactView, chunkDetail, chunkEscalationView, chunkNeighborhoodView, chunkNeighborView, decisionView, prView, questionView, transitionView } from 'fleet/testing';
import type { ChunkStatus } from '../../api/hub';
import { projectChunkDetail } from './chunk-detail.query';

describe('projectChunkDetail', () => {
  it('projects unknown and absent statuses without changing the cached wire aggregate', () => {
    const wire = chunkDetail({
      status: 'future' as ChunkStatus,
      neighborhood: chunkNeighborhoodView({
        prerequisites: [chunkNeighborView({ status: 'paused' })],
        dependents: [chunkNeighborView({ status: null })],
      }),
    });
    const projected = projectChunkDetail(wire);
    expect(projected.status).toEqual({ kind: 'unknown', raw: 'future' });
    expect(projected.neighborhood.prerequisites[0].laneStatus).toEqual({ kind: 'paused' });
    expect(projected.neighborhood.dependents[0].laneStatus).toBeNull();
    expect(wire).not.toHaveProperty('laneStatus');
    expect(wire.neighborhood.prerequisites[0]).not.toHaveProperty('laneStatus');
  });

  it('copies artifact records and selects domain artifacts without changing the cache', () => {
    const artifact = assetArtifactView();
    const wire = chunkDetail({ artifacts: [artifact] });
    const projected = projectChunkDetail(wire);
    expect(projected.artifacts).not.toBe(wire.artifacts);
    expect(projected.artifacts[0]).not.toBe(wire.artifacts[0]);
    expect(projected.artifacts).toEqual([{ ...artifact, kind: { kind: 'asset' } }]);
    expect(projectChunkDetail(chunkDetail({ artifacts: [] })).artifacts).toEqual([]);
  });
  it('copies history and projects transcript history and current-step facts', () => {
    const history = [transitionView({ from_node_id: 'build', from_node_name: 'Build', epoch: 7, reason: 'Assessment passed' })];
    const wire = chunkDetail({ history, current_node_id: 'review', current_node_name: null, latest_epoch: 8, current_node_terminal: false });
    const projected = projectChunkDetail(wire);
    expect(projected.history).toEqual([{
      choice_name: history[0].choice_name,
      epoch: 7,
      from_node_id: 'build',
      from_node_name: 'Build',
      graph_id: history[0].graph_id,
      graph_name: history[0].graph_name,
      recorded_at: history[0].recorded_at,
      to_node_id: history[0].to_node_id,
      to_node_name: history[0].to_node_name,
    }]);
    expect(history[0].reason).toBe('Assessment passed');
    expect(projected.history).not.toBe(history);
    expect(projected.history[0]).not.toBe(history[0]);
    expect(projected.transcriptContext).toEqual({
      history: [{ from_node_id: 'build', from_node_name: 'Build', epoch: 7 }],
      current: { nodeId: 'review', nodeName: null, epoch: 8, terminal: false },
    });
    expect(projected.transcriptContext.history[0]).not.toBe(history[0]);
    expect(projectChunkDetail(chunkDetail({ history: [] })).transcriptContext.history).toEqual([]);
  });

  it('composes the awaiting-human projection at the wire read', () => {
    const wire = chunkDetail({ questions: [questionView({ answered: false })] });
    const projected = projectChunkDetail(wire);
    expect(projected.awaitingHuman.chunk_id).toBe(wire.chunk_id);
    expect(projected.awaitingHuman.terminal).toBe(wire.terminal);
    expect(projected.awaitingHuman.open_questions).toHaveLength(1);
    expect(projected.awaitingHuman.open_questions[0]).not.toBe(wire.questions[0]);
  });

  it('copies escalation facts without retaining the cached nested object', () => {
    const wire = chunkDetail({ escalation: chunkEscalationView({ cause: 'future-cause' }) });
    const projected = projectChunkDetail(wire);
    expect(projected.escalation).toEqual(wire.escalation);
    expect(projected.escalation).not.toBe(wire.escalation);
  });

  it('copies nested delivery, decision and graph history without retaining wire objects', () => {
    const wire = chunkDetail({
      open_prs: [prView()],
      decision: decisionView(),
      history: [transitionView()],
      questions: [questionView()],
    });
    const result = projectChunkDetail(wire);
    expect(result.open_prs).toEqual(wire.open_prs);
    expect(result.open_prs[0]).not.toBe(wire.open_prs[0]);
    expect(result.awaitingHuman.live_decision?.decision_id).toEqual(wire.decision!.decision_id);
    expect(result.awaitingHuman.live_decision!.choices[0]).not.toBe(wire.decision!.choices[0]);
    expect(result.awaitingHuman.open_questions[0].question_id).toEqual(wire.questions[0].question_id);
    expect(result.awaitingHuman.open_questions[0]).not.toBe(wire.questions[0]);
    expect(result).not.toHaveProperty('decision');
    expect(result).not.toHaveProperty('questions');
  });

});

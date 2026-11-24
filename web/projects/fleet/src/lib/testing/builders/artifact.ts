import type {
  AddFindingOp,
  FindingCandidate,
  FindingDelta,
  FindingSurvey,
  GoneFindingOp,
  ObservedFindingOp,
} from '../../chunk/chunk-detail/artifact-structured.model';
import type { ChunkArtifact } from '../../chunk/chunk-detail/artifact.model';
import { fixtureInstant } from './clock';

/** An asset whose content is plain text. */
export function assetChunkArtifact(overrides: Partial<ChunkArtifact> = {}): ChunkArtifact {
  return {
    kind: { kind: 'asset' },
    key: 'plan',
    name: 'plan',
    epoch: 1,
    node_id: 'nd_build',
    node_name: 'build',
    recorded_at: fixtureInstant(-30),
    produced_at: null,
    content: 'Read the work item, then implement the change.',
    repo: null,
    commit_hash: null,
    branch_name: null,
    branch_url: null,
    ...overrides,
  };
}

/** A `git_commit` artifact with its repository, commit hash, and branch. */
export function commitChunkArtifact(overrides: Partial<ChunkArtifact> = {}): ChunkArtifact {
  return {
    kind: { kind: 'git_commit' },
    key: 'commit:blizzard',
    name: 'commit',
    epoch: 1,
    node_id: 'nd_build',
    node_name: 'build',
    recorded_at: fixtureInstant(-30),
    produced_at: null,
    repo: 'blizzard',
    commit_hash: '9f2c4e1a7b3d5860fedcba9876543210abcdef12',
    branch_name: 'feat/fixture',
    branch_url: 'https://github.com/paul-gross/blizzard/tree/feat/fixture',
    content: null,
    ...overrides,
  };
}

/** A candidate finding a survey lists. */
export function domainFindingCandidate(overrides: Partial<FindingCandidate> = {}): FindingCandidate {
  return {
    class: 'drift',
    locus: 'web/projects/fleet/src/lib/chunk',
    summary: 'A view re-types a wire shape the generated client already owns.',
    ref: null,
    introduced: null,
    ...overrides,
  };
}

/** The content of a garden survey's asset. */
export function domainFindingSurvey(overrides: Partial<FindingSurvey> = {}): FindingSurvey {
  return {
    scope: 'web',
    revisions: { blizzard: '9f2c4e1a7b3d' },
    measurement: null,
    candidates: [domainFindingCandidate()],
    ...overrides,
  };
}

/** An `add` op of a finding delta. */
export function domainAddFindingOp(overrides: Partial<AddFindingOp> = {}): AddFindingOp {
  return {
    op: 'add',
    class: 'drift',
    locus: 'web/projects/fleet/src/lib/chunk',
    summary: 'A view re-types a wire shape the generated client already owns.',
    ref: null,
    introduced: null,
    ...overrides,
  };
}

/** An `observed` op of a finding delta. */
export function domainObservedFindingOp(overrides: Partial<ObservedFindingOp> = {}): ObservedFindingOp {
  return {
    op: 'observed',
    id: 'fn_01fixture0000000000000000000',
    ...overrides,
  };
}

/** A `gone` op of a finding delta. */
export function domainGoneFindingOp(overrides: Partial<GoneFindingOp> = {}): GoneFindingOp {
  return {
    op: 'gone',
    id: 'fn_01fixture0000000000000000001',
    note: 'The view now reads the generated type.',
    ...overrides,
  };
}

/** The content of a garden routine's delta asset. */
export function domainFindingDelta(overrides: Partial<FindingDelta> = {}): FindingDelta {
  return {
    scope: 'web',
    revisions: { blizzard: '9f2c4e1a7b3d' },
    measurement: null,
    findings: [domainAddFindingOp()],
    ...overrides,
  };
}

/** An asset whose content is `survey` serialized — `ChunkArtifactBody` reads it as a survey. */
export function surveyChunkArtifact(
  survey: FindingSurvey = domainFindingSurvey(),
  overrides: Partial<ChunkArtifact> = {},
): ChunkArtifact {
  return assetChunkArtifact({
    key: 'survey',
    name: 'survey',
    content: JSON.stringify(survey),
    ...overrides,
  });
}

/** An asset whose content is `delta` serialized — `ChunkArtifactBody` reads it as a delta. */
export function deltaChunkArtifact(
  delta: FindingDelta = domainFindingDelta(),
  overrides: Partial<ChunkArtifact> = {},
): ChunkArtifact {
  return assetChunkArtifact({
    key: 'delta',
    name: 'delta',
    content: JSON.stringify(delta),
    ...overrides,
  });
}

/** An asset keyed `bounce-envelope` whose content serializes a plain record of `cause`,
 * `detail`, and any further keys. */
export function bounceChunkArtifact(
  envelope: Record<string, unknown> = {
    cause: 'missing_artifact',
    detail: 'The step ended without declaring `commit`.',
  },
  overrides: Partial<ChunkArtifact> = {},
): ChunkArtifact {
  return assetChunkArtifact({
    key: 'bounce-envelope',
    name: 'bounce-envelope',
    content: JSON.stringify(envelope),
    ...overrides,
  });
}

import type { ArtifactView } from '../../api/hub';
import { openLookup } from '../../core/open-lookup';
import type { ChunkArtifact } from './artifact.model';

const artifactKind = openLookup<ArtifactView['kind'], ChunkArtifact['kind']>({
  asset: { kind: 'asset' },
  git_commit: { kind: 'git_commit' },
}, (raw) => ({ kind: 'unknown', raw }));

/** Copy the wire fields into the shared domain without mutating the cached response. */
export function fromArtifactView(wire: ArtifactView): ChunkArtifact {
  return {
    kind: artifactKind(wire.kind),
    key: wire.key,
    name: wire.name,
    epoch: wire.epoch,
    node_id: wire.node_id,
    node_name: wire.node_name,
    recorded_at: wire.recorded_at,
    produced_at: wire.produced_at,
    content: wire.content,
    repo: wire.repo,
    commit_hash: wire.commit_hash,
    branch_name: wire.branch_name,
    branch_url: wire.branch_url,
  };
}

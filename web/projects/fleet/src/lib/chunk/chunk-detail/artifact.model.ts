/** A chunk artifact, independent of either daemon's transport. */
export interface ChunkArtifact {
  kind: { kind: 'asset' } | { kind: 'git_commit' } | { kind: 'unknown'; raw: string };
  key: string;
  name: string;
  epoch: number;
  node_id: string;
  node_name: string;
  recorded_at: string | null;
  produced_at: string | null;
  content: string | null;
  repo: string | null;
  commit_hash: string | null;
  branch_name: string | null;
  branch_url: string | null;
}

/** Artifacts attached to a chunk, for views that only need the store. */
export interface ChunkArtifactStore {
  chunk_id: string;
  artifacts: readonly ChunkArtifact[];
}

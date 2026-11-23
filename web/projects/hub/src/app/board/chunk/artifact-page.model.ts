import { type ChunkArtifact } from 'fleet';

/** The artifact `key` names among `artifacts`, or `undefined` when there is no key or it names nothing. */
export function artifactByKey(artifacts: readonly ChunkArtifact[], key: string | null): ChunkArtifact | undefined {
  if (key === null) return undefined;
  return artifacts.find((art) => art.key === key);
}

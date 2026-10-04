import { type ArtifactView } from 'fleet';

/** The artifact `key` names among `artifacts`, or `undefined` when there is no key or it names nothing. */
export function artifactByKey(artifacts: readonly ArtifactView[], key: string | null): ArtifactView | undefined {
  if (key === null) return undefined;
  return artifacts.find((art) => art.key === key);
}

import type { ArtifactView } from '../api/hub';

/**
 * The chunk artifact store, ordered oldest attachment first — by `recorded_at`
 * (the ULID-decoded attachment instant). Entries without a stamp keep the
 * server's store-key order among themselves.
 *
 * The single owner of that order (`canon:one-owner`) — the desktop dock's list
 * and the chunk detail page's Artifacts tab nav both sort through this so
 * neither can drift from the other.
 */
export function sortArtifacts(artifacts: readonly ArtifactView[]): ArtifactView[] {
  return [...artifacts].sort((a, b) => (a.recorded_at ?? '').localeCompare(b.recorded_at ?? ''));
}

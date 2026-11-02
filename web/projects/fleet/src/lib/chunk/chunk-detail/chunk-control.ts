import type { Signal } from '@angular/core';

/** The chunk-control port behind a chunk header's ⋮ menu ({@link ChunkActionsMenu}) —
 * Pause/Resume, Detach, Complete, Delete. Each pending read is scoped to one chunk id;
 * the daemon that builds the port reports failures on its own notice channel. */
export interface ChunkControl {
  /** Whether the identity may operate the chunk (`chunk:control`). */
  readonly canControl: Signal<boolean>;
  pausePending(chunkId: string): boolean;
  detachPending(chunkId: string): boolean;
  completePending(chunkId: string): boolean;
  deletePending(chunkId: string): boolean;
  pause(chunkId: string): void;
  resume(chunkId: string): void;
  detach(chunkId: string): void;
  complete(chunkId: string): void;
  /** Delete the chunk; `onDeleted` runs once the hub confirms it. */
  delete(chunkId: string, onDeleted?: () => void): void;
}

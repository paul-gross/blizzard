import type { BacklogPeekEntry, QueuePeekEntry } from 'fleet';
import type { BoardOrderEntry } from './queue.domain';

export function fromBoardOrderEntry(entry: QueuePeekEntry | BacklogPeekEntry): BoardOrderEntry {
  return { chunk_id: entry.chunk_id };
}

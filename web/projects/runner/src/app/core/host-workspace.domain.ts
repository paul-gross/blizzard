import type { HostRecordFacts } from './host-record.domain';

/** A workspace the host declares, with its environment ceiling and its record's revision, state, and settings. */
export interface HostWorkspace extends HostRecordFacts {
  readonly workspace_id: string;
  readonly max_environments: number;
}

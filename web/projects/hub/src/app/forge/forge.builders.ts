// The forge domain builders specs build their inputs from — domain literals only, no wire, no Angular.
import type { Forge, ForgeRecord } from './forge.domain';

/** An enabled GitHub forge at its second revision, deriving its web origin from its API base. */
export function forge(overrides: Partial<Forge> = {}): Forge {
  return {
    id: 'frg_1',
    slug: 'github',
    name: 'GitHub',
    kind: 'github',
    apiBase: 'https://api.github.com',
    webBase: null,
    webOrigin: 'https://github.com',
    revision: 2,
    retired: false,
    ...overrides,
  };
}

/** {@link forge} as its detail reads it, with nothing referring to it and no budget reading. */
export function forgeRecord(overrides: Partial<ForgeRecord> = {}): ForgeRecord {
  return {
    ...forge(),
    createdBy: 'usr_1',
    createdByName: 'Ana',
    offered: [],
    budgets: [],
    repositories: [],
    workSources: [],
    ...overrides,
  };
}

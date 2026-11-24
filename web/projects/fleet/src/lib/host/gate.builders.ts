import type { UnenforceableGate } from './gate.domain';

export function unenforceableGate(overrides: Partial<UnenforceableGate> = {}): UnenforceableGate {
  return { node: 'deliver', reason: 'hub-executed', ...overrides };
}

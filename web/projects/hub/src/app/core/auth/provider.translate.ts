import type { ProviderSummary } from 'fleet/shell';

import type { AuthProvider } from './provider.domain';

export function fromProviderSummary(wire: ProviderSummary): AuthProvider {
  return { name: wire.name, display_name: wire.display_name, type: wire.type };
}

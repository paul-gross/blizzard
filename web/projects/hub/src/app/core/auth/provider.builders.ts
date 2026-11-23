import type { AuthProvider } from './provider.domain';

export function authProvider(overrides: Partial<AuthProvider> = {}): AuthProvider {
  return { name: 'github', display_name: 'GitHub', type: 'github', ...overrides };
}

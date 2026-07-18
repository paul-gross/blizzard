import { compactRef } from './compact-ref';

describe('compactRef', () => {
  it('renders a registered prefix as its sigil plus the ULID tail', () => {
    // The ULID's entropy is in its tail (timestamp leads), so the tail is what
    // discriminates ids minted close together.
    expect(compactRef('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9')).toBe('C-3YJ9');
    expect(compactRef('lease_01KXKVVF1J3D6H6VYZ3XYNZPRR')).toBe('L-ZPRR');
    expect(compactRef('qn_01KXKVVF1J3D6H6VYZ3XYN3Q77')).toBe('Q-3Q77');
  });

  it('falls back to the uppercased first letter for an unregistered prefix', () => {
    expect(compactRef('zz_01KXKVVF1J3D6H6VYZ3XYN3YJ9')).toBe('Z-3YJ9');
  });

  it('passes a non-prefixed name through unchanged', () => {
    // Env pool names and runner ids are already human-scale, not prefixed ULIDs.
    expect(compactRef('e1')).toBe('e1');
    expect(compactRef('runner-local')).toBe('runner-local');
  });

  it('keeps prefixes with colliding first letters apart via the registry', () => {
    expect(compactRef('cho_01KXKVVF1J3D6H6VYZ3XYN3YJ9')).toBe('CH-3YJ9');
    expect(compactRef('ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9')).toBe('C-3YJ9');
  });
});

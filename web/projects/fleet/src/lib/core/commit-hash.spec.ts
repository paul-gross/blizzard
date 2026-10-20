import { SHORT_HASH_LENGTH, shortCommitHash } from './commit-hash';

describe('shortCommitHash', () => {
  it("keeps a full hash's leading seven characters", () => {
    expect(shortCommitHash('9f2c4e1a7b3d5860fedcba9876543210abcdef12')).toBe('9f2c4e1');
    expect(SHORT_HASH_LENGTH).toBe(7);
  });

  it('leaves a hash already no longer than the short form whole', () => {
    expect(shortCommitHash('9f2c4')).toBe('9f2c4');
  });
});

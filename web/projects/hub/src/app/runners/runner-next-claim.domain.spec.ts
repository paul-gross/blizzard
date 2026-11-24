import { describe, expect, it } from 'vitest';
import { laneStatusWord, passReasonWord } from './runner-next-claim.domain';

describe('next claim words', () => {
  it('reads a known pass reason as itself and an unknown one raw', () => {
    expect(passReasonWord('blocked')).toBe('blocked');
    expect(passReasonWord({ kind: 'unknown', raw: 'quarantined' })).toBe('quarantined');
  });

  it('reads a known lane status as itself and an unknown one raw', () => {
    expect(laneStatusWord({ kind: 'needs_human' })).toBe('needs_human');
    expect(laneStatusWord({ kind: 'unknown', raw: 'orbiting' })).toBe('orbiting');
  });
});

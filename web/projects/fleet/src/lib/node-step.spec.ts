import { nodeStepKey, parseNodeStepKey } from './node-step';

describe('nodeStepKey/parseNodeStepKey', () => {
  it('round-trips a node id and epoch through the key', () => {
    const key = nodeStepKey('nd_build', 3);
    expect(key).toBe('nd_build:3');
    expect(parseNodeStepKey(key)).toEqual({ nodeId: 'nd_build', epoch: 3 });
  });

  it('splits on the last colon, so a node id carrying one round-trips too', () => {
    // Not a shape any real node id has today, but the codec should not assume it never will.
    const key = nodeStepKey('nd:weird', 1);
    expect(parseNodeStepKey(key)).toEqual({ nodeId: 'nd:weird', epoch: 1 });
  });

  it('returns null for a string with no colon', () => {
    expect(parseNodeStepKey('nd_build')).toBeNull();
  });

  it('returns null for a non-integer epoch', () => {
    expect(parseNodeStepKey('nd_build:abc')).toBeNull();
    expect(parseNodeStepKey('nd_build:1.5')).toBeNull();
  });

  it('returns null for an empty node id', () => {
    expect(parseNodeStepKey(':3')).toBeNull();
  });
});

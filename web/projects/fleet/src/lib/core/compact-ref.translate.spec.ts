import type { IdPrefix } from '../api/hub';
import type { KnownEntityPrefix } from './compact-ref';

// Keep the display registry exhaustive when code generation adds an entity prefix.
const allWirePrefixesHaveDisplay: Exclude<IdPrefix, KnownEntityPrefix> extends never ? true : never = true;

describe('compact ref wire vocabulary', () => {
  it('covers every generated prefix', () => {
    expect(allWirePrefixesHaveDisplay).toBe(true);
  });
});

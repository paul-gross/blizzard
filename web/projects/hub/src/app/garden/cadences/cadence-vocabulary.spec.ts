import { CadenceAnchor } from 'fleet';
import { describe, expect, it } from 'vitest';

import { CADENCE_ANCHORS } from './cadence-vocabulary';

describe('CADENCE_ANCHORS', () => {
  it('is the generated anchor vocabulary', () => {
    expect([...CADENCE_ANCHORS]).toEqual([CadenceAnchor.SCHEDULED, CadenceAnchor.STARTED, CadenceAnchor.FINISHED]);
  });
});

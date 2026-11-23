import { describe, expect, it } from 'vitest';

import { narrowEventSeverity } from './event-log.domain';

describe('narrowEventSeverity', () => {
  it('passes a listed severity and refuses anything else', () => {
    expect(narrowEventSeverity('warning')).toBe('warning');
    expect(narrowEventSeverity('')).toBeNull();
    expect(narrowEventSeverity('emergency')).toBeNull();
  });
});

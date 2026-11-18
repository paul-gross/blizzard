import { describe, expect, it } from 'vitest';
import { workSourceSummary } from 'fleet/testing';
import { toProjectWorkSource } from './work-sources.translate';

describe('project work-source projection', () => {
  it('preserves built-in identity and retirement for link choices', () => {
    const source = { work_source_id: null, slug: null, name: 'hub', provider: null, locator: null, retired: false, built_in: true };
    expect(toProjectWorkSource(workSourceSummary(source))).toEqual(source);
  });
});

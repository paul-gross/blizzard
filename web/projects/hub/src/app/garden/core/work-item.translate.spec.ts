import { gardenWorkItemView } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { fromWorkItemView } from './work-item.translate';

describe('fromWorkItemView', () => {
  it('reads the pointer, label, and link', () => {
    expect(fromWorkItemView(gardenWorkItemView({ web_url: 'https://x/42' }))).toEqual({
      source: 'hub',
      ref: '42',
      label: 'hub#42',
      webUrl: 'https://x/42',
    });
  });

  it("keeps a missing label and a terminal item's link null", () => {
    expect(fromWorkItemView(gardenWorkItemView({ label: null, web_url: null }))).toMatchObject({ label: null, webUrl: null });
  });
});

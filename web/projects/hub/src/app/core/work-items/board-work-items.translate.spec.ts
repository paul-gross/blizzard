import { workItemEntry } from 'fleet/testing';
import { fromBoardWorkItems } from './board-work-items.translate';

describe('fromBoardWorkItems', () => {
  it('keeps display facts while excluding the transport and detail-only fields', () => {
    const item = workItemEntry({ source: 'hub', ref: '1', title: 'Fix it', label: 'hub:1', web_url: 'https://example.test/1' });
    expect(fromBoardWorkItems({ items: [item] })).toEqual({
      items: [{ source: 'hub', ref: '1', title: 'Fix it', label: 'hub:1', web_url: 'https://example.test/1' }],
    });
  });
});

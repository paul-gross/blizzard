import { chunkProgress } from '../fixtures';
import { expect, test } from '@playwright/test';

const places = (...states: string[]) => ({
  status: 'running',
  flow_places: states.map((state, index) => ({ flow_id: `flow-${index}`, state })),
}) as Parameters<typeof chunkProgress>[0];

test.describe('chunkProgress reads flow places as the hub classifies them', () => {
  test('reads next, waiting and polling as one in-line place', () => {
    expect(chunkProgress(places('next', 'waiting', 'polling')).places?.map(([, place]) => place)).toEqual(['in-line', 'in-line', 'in-line']);
  });

  test('reads a landing place as not in line', () => {
    expect(chunkProgress(places('landing')).places).toEqual([['flow-0', 'landing']]);
  });
});

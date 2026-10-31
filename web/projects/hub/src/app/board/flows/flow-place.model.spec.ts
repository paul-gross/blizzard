import type { ChunkFlowPlaceView } from 'fleet';

import { flowPath, ordinal, placeLines, placeStateLabel, placeStateTone, placeText } from './flow-place.model';

const place = (over: Partial<ChunkFlowPlaceView> = {}): ChunkFlowPlaceView => ({
  flow_id: 'flw_1',
  resource: 'repo_1:master',
  repository_name: 'blizzard',
  repository_slug: 'paul-gross/blizzard',
  branch: 'master',
  state: 'waiting',
  position: 3,
  next_poll_at: null,
  ...over,
});

describe('ordinal', () => {
  it('suffixes each count as English does, the teens included', () => {
    expect([1, 2, 3, 4, 11, 12, 13, 21, 22, 23, 101, 111].map(ordinal)).toEqual([
      '1st', '2nd', '3rd', '4th', '11th', '12th', '13th', '21st', '22nd', '23rd', '101st', '111th',
    ]);
  });
});

describe('placeText', () => {
  it('says a chunk whose turn is open is landing on the branch', () => {
    expect(placeText(place({ state: 'landing', position: 1 }))).toBe('landing on blizzard/master');
  });

  it('says every other chunk in line by its position', () => {
    expect(placeText(place({ state: 'waiting', position: 3 }))).toBe('3rd on blizzard/master');
    expect(placeText(place({ state: 'next', position: 1 }))).toBe('1st on blizzard/master');
    expect(placeText(place({ state: 'polling', position: 2 }))).toBe('2nd on blizzard/master');
  });

  it('says a held or paused chunk is passed over', () => {
    expect(placeText(place({ state: 'passed-over' }))).toBe('passed over on blizzard/master');
  });

  it('places a state this build does not know by its position', () => {
    expect(placeText(place({ state: 'future-state' as ChunkFlowPlaceView['state'], position: 2 }))).toBe('2nd on blizzard/master');
  });
});

describe('flowPath', () => {
  it('names the repository by its name, else its slug, else falls back to the resource', () => {
    expect(flowPath(place())).toBe('blizzard/master');
    expect(flowPath(place({ repository_name: null }))).toBe('paul-gross/blizzard/master');
    expect(flowPath(place({ repository_name: null, repository_slug: null }))).toBe('repo_1:master');
  });
});

describe('place state look', () => {
  it('labels and tones each known state', () => {
    expect(placeStateLabel('passed-over')).toBe('passed over');
    expect(placeStateTone('landing')).toBe('running');
    expect(placeStateTone('passed-over')).toBe('idle');
  });

  it('reads a state this build does not know raw, in the idle tone', () => {
    expect(placeStateLabel('future-state')).toBe('future-state');
    expect(placeStateTone('future-state')).toBe('idle');
    expect(placeStateLabel('awaiting-merge')).toBe('awaiting merge');
    expect(placeStateTone('awaiting-merge')).toBe('waiting');
  });
});

describe('placeLines', () => {
  it('gives one line per flow, in the order the hub lists them, and none for a chunk with no place', () => {
    const lines = placeLines([place({ state: 'landing', position: 1 }), place({ flow_id: 'flw_2', branch: 'release', position: 2 })]);
    expect(lines.map((line) => line.text)).toEqual(['landing on blizzard/master', '2nd on blizzard/release']);
    expect(lines.map((line) => line.flowId)).toEqual(['flw_1', 'flw_2']);
    expect(placeLines(undefined)).toEqual([]);
  });
});

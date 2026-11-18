import { describe, expect, it } from 'vitest';

import { gardenCadence, gardenCadenceAttention, gardenChore } from '../core/cadence.builders';
import {
  cadenceRows,
  errandCounts,
  flagOffLine,
  CADENCES_FLAG_OFF_LINE,
  presentCadenceSlug,
  rhythmText,
  spacingText,
} from './gardening-cadences-page.model';

describe('spacingText', () => {
  it('names the largest whole unit', () => {
    expect(spacingText(10 * 1440)).toBe('10 days');
    expect(spacingText(1440)).toBe('1 day');
    expect(spacingText(360)).toBe('6 hours');
    expect(spacingText(90)).toBe('90 minutes');
    expect(spacingText(1)).toBe('1 minute');
  });
});

describe('rhythmText', () => {
  it('states the schedule then the spacing', () => {
    expect(rhythmText(gardenCadence({ slug: 'evening' }))).toBe('0 20 * * * America/Chicago, at least 10 days apart');
  });

  it('names an anchor other than the start', () => {
    expect(rhythmText({ schedule: null, spacingMinutes: 60, anchor: 'finished' })).toBe('at least 1 hour apart from finished');
    expect(rhythmText({ schedule: null, spacingMinutes: 60, anchor: 'started' })).toBe('at least 1 hour apart');
    expect(rhythmText({ schedule: null, spacingMinutes: 60, anchor: null })).toBe('at least 1 hour apart');
    expect(rhythmText({ schedule: null, spacingMinutes: 60, anchor: 'scheduled' })).toBe('at least 1 hour apart from scheduled');
    expect(rhythmText({ schedule: null, spacingMinutes: 60, anchor: { unlisted: 'sideways' } })).toBe(
      'at least 1 hour apart from sideways',
    );
  });

  it('states a schedule alone, and says when there is no rhythm', () => {
    expect(rhythmText({ schedule: { cron: '0 3 * * 1', zone: 'UTC' }, spacingMinutes: null, anchor: null })).toBe('0 3 * * 1 UTC');
    expect(rhythmText({ schedule: null, spacingMinutes: null, anchor: null })).toBe('no rhythm');
  });
});

describe('errandCounts', () => {
  it('counts the errands by state in order, leaving out the states holding none', () => {
    const cadence = gardenCadence({ slug: 'evening', errandCounts: { due: 2, running: 0, waiting: 3, stopped: 1 } });
    expect(errandCounts(cadence)).toEqual([
      { state: 'due', count: 2, label: 'due', tone: 'spawning' },
      { state: 'waiting', count: 3, label: 'waiting', tone: 'waiting' },
      { state: 'stopped', count: 1, label: 'stopped', tone: 'stale' },
    ]);
  });
});

describe('cadenceRows', () => {
  it('carries the rhythm, next moment, chore count, counts, and attention of each cadence', () => {
    const cadence = gardenCadence({
      slug: 'evening',
      name: 'Evening sweep',
      chores: [gardenChore(), gardenChore({ id: 'chr_2', key: 'weekly' })],
      attention: [gardenCadenceAttention()],
      errandCounts: { due: 1, running: 0, waiting: 0, stopped: 0 },
    });
    const [row] = cadenceRows([cadence]);
    expect(row).toEqual({
      slug: 'evening',
      name: 'Evening sweep',
      retired: false,
      rhythm: '0 20 * * * America/Chicago, at least 10 days apart',
      nextMoment: cadence.nextMoments[0],
      choreCount: 2,
      counts: [{ state: 'due', count: 1, label: 'due', tone: 'spawning' }],
      attentionCount: 1,
    });
  });

  it('has no next moment for a cadence the hub names none for', () => {
    const [row] = cadenceRows([gardenCadence({ slug: 'off', retired: true, nextMoments: [] })]);
    expect(row.nextMoment).toBeNull();
    expect(row.retired).toBe(true);
  });
});

describe('flagOffLine', () => {
  it('says cadences mint nothing only while the flag is off', () => {
    expect(flagOffLine(false)).toBe(CADENCES_FLAG_OFF_LINE);
    expect(flagOffLine(true)).toBeNull();
  });
});

describe('presentCadenceSlug', () => {
  it('keeps a slug the list holds, and drops one it does not', () => {
    const cadences = [gardenCadence({ slug: 'evening' })];
    expect(presentCadenceSlug('evening', cadences)).toBe('evening');
    expect(presentCadenceSlug('gone', cadences)).toBeNull();
    expect(presentCadenceSlug(null, cadences)).toBeNull();
  });
});

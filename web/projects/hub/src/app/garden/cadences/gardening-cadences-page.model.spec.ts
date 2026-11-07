import type { CadenceAnchor } from 'fleet';
import { gardenCadenceAttentionView, gardenCadenceView, gardenChoreView } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import {
  cadenceRows,
  createCadenceRequest,
  EMPTY_CADENCE_DRAFT,
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
    expect(rhythmText(gardenCadenceView({ slug: 'evening' }))).toBe('0 20 * * * America/Chicago, at least 10 days apart');
  });

  it('names an anchor other than the start', () => {
    expect(rhythmText({ schedule: null, spacing_minutes: 60, anchor: 'finished' })).toBe('at least 1 hour apart from finished');
    expect(rhythmText({ schedule: null, spacing_minutes: 60, anchor: 'started' })).toBe('at least 1 hour apart');
    expect(rhythmText({ schedule: null, spacing_minutes: 60, anchor: null })).toBe('at least 1 hour apart');
    expect(rhythmText({ schedule: null, spacing_minutes: 60, anchor: 'scheduled' })).toBe('at least 1 hour apart from scheduled');
    expect(rhythmText({ schedule: null, spacing_minutes: 60, anchor: 'sideways' as CadenceAnchor })).toBe(
      'at least 1 hour apart from sideways',
    );
  });

  it('states a schedule alone, and says when there is no rhythm', () => {
    expect(rhythmText({ schedule: { cron: '0 3 * * 1', zone: 'UTC' }, spacing_minutes: null, anchor: null })).toBe('0 3 * * 1 UTC');
    expect(rhythmText({ schedule: null, spacing_minutes: null, anchor: null })).toBe('no rhythm');
  });
});

describe('errandCounts', () => {
  it('counts the errands by state in order, leaving out the states holding none', () => {
    const cadence = gardenCadenceView({ slug: 'evening', errand_counts: { due: 2, running: 0, waiting: 3, stopped: 1 } });
    expect(errandCounts(cadence)).toEqual([
      { state: 'due', count: 2, label: 'due', tone: 'spawning' },
      { state: 'waiting', count: 3, label: 'waiting', tone: 'waiting' },
      { state: 'stopped', count: 1, label: 'stopped', tone: 'stale' },
    ]);
  });
});

describe('cadenceRows', () => {
  it('carries the rhythm, next moment, chore count, counts, and attention of each cadence', () => {
    const cadence = gardenCadenceView({
      slug: 'evening',
      name: 'Evening sweep',
      chores: [gardenChoreView(), gardenChoreView({ chore_id: 'chr_2', key: 'weekly' })],
      attention: [gardenCadenceAttentionView()],
      errand_counts: { due: 1, running: 0, waiting: 0, stopped: 0 },
    });
    const [row] = cadenceRows([cadence]);
    expect(row).toEqual({
      slug: 'evening',
      name: 'Evening sweep',
      retired: false,
      rhythm: '0 20 * * * America/Chicago, at least 10 days apart',
      nextMoment: cadence.next_moments[0],
      choreCount: 2,
      counts: [{ state: 'due', count: 1, label: 'due', tone: 'spawning' }],
      attentionCount: 1,
    });
  });

  it('has no next moment for a cadence the hub names none for', () => {
    const [row] = cadenceRows([gardenCadenceView({ slug: 'off', retired: true, next_moments: [] })]);
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
    const cadences = [gardenCadenceView({ slug: 'evening' })];
    expect(presentCadenceSlug('evening', cadences)).toBe('evening');
    expect(presentCadenceSlug('gone', cadences)).toBeNull();
    expect(presentCadenceSlug(null, cadences)).toBeNull();
  });
});

describe('createCadenceRequest', () => {
  it('sends every field typed', () => {
    expect(
      createCadenceRequest({
        slug: ' evening ',
        name: 'Evening',
        cron: '0 20 * * *',
        zone: 'America/Chicago',
        spacing: '10d',
        anchor: 'finished',
      }),
    ).toEqual({
      slug: 'evening',
      name: 'Evening',
      schedule: { cron: '0 20 * * *', zone: 'America/Chicago' },
      spacing: '10d',
      anchor: 'finished',
    });
  });

  it('leaves a blank field out', () => {
    expect(createCadenceRequest({ ...EMPTY_CADENCE_DRAFT, slug: 'daily', spacing: '1d' })).toEqual({ slug: 'daily', spacing: '1d' });
  });

  it('sends a half-typed schedule for the hub to refuse naming the missing half', () => {
    expect(createCadenceRequest({ ...EMPTY_CADENCE_DRAFT, slug: 'x', cron: '0 1 * * *' }).schedule).toEqual({
      cron: '0 1 * * *',
      zone: null,
    });
  });
});

import { describe, expect, it } from 'vitest';

import {
  filterByLifecycle,
  includeRetired,
  lifecycleEmptyText,
  lifecycleFilterParam,
  parseLifecycleFilter,
} from './config-filter.model';

const LIVE: { name: string; retired?: boolean } = { name: 'live' };
const OFF = { name: 'off', retired: true };
const ON = { name: 'on', retired: false };

describe('parseLifecycleFilter', () => {
  it('defaults an absent or unknown param to active', () => {
    expect(parseLifecycleFilter(null)).toBe('active');
    expect(parseLifecycleFilter('bogus')).toBe('active');
  });

  it('reads retired and all', () => {
    expect(parseLifecycleFilter('retired')).toBe('retired');
    expect(parseLifecycleFilter('all')).toBe('all');
  });
});

describe('lifecycleFilterParam', () => {
  it('drops the param for the default filter', () => {
    expect(lifecycleFilterParam('active')).toBeNull();
  });

  it('carries every other filter', () => {
    expect(lifecycleFilterParam('retired')).toBe('retired');
    expect(lifecycleFilterParam('all')).toBe('all');
  });
});

describe('includeRetired', () => {
  it('asks for retired records only off the active filter', () => {
    expect(includeRetired('active')).toBe(false);
    expect(includeRetired('retired')).toBe(true);
    expect(includeRetired('all')).toBe(true);
  });
});

describe('filterByLifecycle', () => {
  it('keeps live records under active, an unset flag reading live', () => {
    expect(filterByLifecycle([LIVE, OFF, ON], 'active')).toEqual([LIVE, ON]);
  });

  it('keeps only retired records under retired', () => {
    expect(filterByLifecycle([LIVE, OFF, ON], 'retired')).toEqual([OFF]);
  });

  it('keeps everything under all', () => {
    expect(filterByLifecycle([LIVE, OFF, ON], 'all')).toEqual([LIVE, OFF, ON]);
  });
});

describe('lifecycleEmptyText', () => {
  it('names the filter in the copy', () => {
    expect(lifecycleEmptyText('secrets', 'active')).toBe('No active secrets.');
    expect(lifecycleEmptyText('secrets', 'retired')).toBe('No retired secrets.');
    expect(lifecycleEmptyText('secrets', 'all')).toBe('No secrets yet.');
  });
});

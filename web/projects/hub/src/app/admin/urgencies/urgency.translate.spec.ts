import { type Urgency, readRefusal } from 'fleet/core';
import { describe, expect, it } from 'vitest';

import type { UrgencyDraft } from './urgency.domain';
import { toCreateRequest, toDraft, toEditBaseline, toEditRequest, URGENCY_REFUSALS } from './urgency.translate';

const DRAFT: UrgencyDraft = { slug: 'soon', name: 'Soon', color: 'red', mark: '🔥' };
const PNG = 'data:image/png;base64,YQ==';
const WEBP = 'data:image/webp;base64,Yg==';
const URGENCY: Urgency = {
  id: 'urg_1', slug: 'soon', name: 'Soon', color: 'red', emoji: '🔥', icon: null,
  retired: false, default: false, revision: 7, openChunks: 0, place: 0, createdAt: '', createdBy: 'operator',
};

describe('toDraft and toEditBaseline', () => {
  it('waits for an icon image and otherwise opens the stored mark', () => {
    const image = { ...URGENCY, emoji: null, icon: { digest: 'abc', mediaType: 'image/png' } };
    expect(toDraft(undefined, null)).toBeNull();
    expect(toDraft(image, undefined)).toBeNull();
    expect(toDraft(image, '')).toBeNull();
    expect(toDraft(image, PNG)).toEqual({ ...DRAFT, mark: PNG });
    expect(toDraft(URGENCY, null)).toEqual(DRAFT);
    expect(toDraft({ ...URGENCY, emoji: null }, null)).toEqual({ ...DRAFT, mark: '' });
  });

  it('captures the read identity and revision and copies the draft', () => {
    const draft = { ...DRAFT };
    const baseline = toEditBaseline(URGENCY, draft);
    expect(baseline).toEqual({ id: 'urg_1', revision: 7, draft: DRAFT });
    draft.name = 'Changed';
    expect(baseline.draft.name).toBe('Soon');
  });
});

describe('toCreateRequest', () => {
  it.each(['/', '/🔥'])('sends slash mark %s as emoji', (mark) => {
    expect(toCreateRequest({ ...DRAFT, mark })).toEqual({ slug: 'soon', name: 'Soon', color: 'red', emoji: mark });
  });
  it('trims required text and omits blank optional color and mark', () => {
    expect(toCreateRequest({ slug: ' soon ', name: ' Soon ', color: '  ', mark: '  ' })).toEqual({ slug: 'soon', name: 'Soon' });
  });

  it('sends a trimmed color and emoji, without an icon', () => {
    expect(toCreateRequest({ ...DRAFT, color: ' red ', mark: ' 🔥 ' })).toEqual({ slug: 'soon', name: 'Soon', color: 'red', emoji: '🔥' });
  });

  it.each([[PNG, 'image/png', 'YQ=='], [WEBP, 'image/webp', 'Yg==']])('sends image %s as an icon, without emoji', (mark, media_type, data) => {
    expect(toCreateRequest({ ...DRAFT, mark: ` ${mark} ` })).toEqual({ slug: 'soon', name: 'Soon', color: 'red', icon: { media_type, data } });
  });
});

describe('toEditRequest', () => {
  it.each(['/', '/🔥'])('preserves or switches to slash mark %s as emoji', (mark) => {
    const before = { ...DRAFT, mark };
    expect(toEditRequest(before, { ...before, name: 'Later' })).toEqual({ name: 'Later' });
    expect(toEditRequest(DRAFT, before)).toEqual({ emoji: mark, icon: null });
    expect(toEditRequest({ ...DRAFT, mark: PNG }, before)).toEqual({ emoji: mark, icon: null });
  });
  it('compares trimmed values, ignores slug and sends no unchanged fields', () => {
    expect(toEditRequest(DRAFT, { slug: 'another', name: ' Soon ', color: ' red ', mark: ' 🔥 ' })).toEqual({});
    expect(toEditRequest({ ...DRAFT, name: ' Soon ', color: ' red ', mark: ' 🔥 ' }, DRAFT)).toEqual({});
  });

  it('sends only changed editable text, including a cleared color', () => {
    expect(toEditRequest(DRAFT, { ...DRAFT, name: ' Later ' })).toEqual({ name: 'Later' });
    expect(toEditRequest(DRAFT, { ...DRAFT, color: '  ' })).toEqual({ color: '' });
    expect(toEditRequest(DRAFT, { ...DRAFT, name: ' Later ', color: ' blue ' })).toEqual({ name: 'Later', color: 'blue' });
  });

  it('clears either mark with explicit nulls', () => {
    expect(toEditRequest(DRAFT, { ...DRAFT, mark: ' ' })).toEqual({ emoji: null, icon: null });
    expect(toEditRequest({ ...DRAFT, mark: PNG }, { ...DRAFT, mark: '' })).toEqual({ emoji: null, icon: null });
    expect(toEditRequest({ ...DRAFT, mark: '' }, { ...DRAFT, mark: ' ' })).toEqual({});
  });

  it('switches marks and clears the other representation', () => {
    expect(toEditRequest(DRAFT, { ...DRAFT, mark: PNG })).toEqual({ emoji: null, icon: { media_type: 'image/png', data: 'YQ==' } });
    expect(toEditRequest({ ...DRAFT, mark: PNG }, DRAFT)).toEqual({ emoji: '🔥', icon: null });
    expect(toEditRequest({ ...DRAFT, mark: PNG }, { ...DRAFT, mark: WEBP })).toEqual({ emoji: null, icon: { media_type: 'image/webp', data: 'Yg==' } });
    expect(toEditRequest({ ...DRAFT, mark: '' }, DRAFT)).toEqual({ emoji: '🔥', icon: null });
  });
});

describe('URGENCY_REFUSALS', () => {
  it.each([
    ['slug', 'slug'], ['name', 'name'], ['color', 'color'], ['emoji', 'mark'],
    ['icon', 'mark'], ['icon.media_type', 'mark'], ['icon.data', 'mark'],
  ] as const)('maps %s to %s', (path, field) => {
    expect(URGENCY_REFUSALS.field(path)).toBe(field);
    expect(readRefusal({ detail: [{ loc: ['body', ...path.split('.')], msg: 'bad' }] }, URGENCY_REFUSALS)).toEqual({
      ok: false, fields: { [field]: 'bad' }, form: null,
    });
  });

  it('routes a taken slug to its field and other problems to the form', () => {
    expect(readRefusal({ type: 'urn:blizzard:problem:urgency-slug-taken', detail: 'Taken.' }, URGENCY_REFUSALS)).toEqual({
      ok: false, fields: { slug: 'Taken.' }, form: null,
    });
    expect(readRefusal({ type: 'urn:blizzard:problem:revision-conflict', detail: 'Moved on.' }, URGENCY_REFUSALS)).toEqual({
      ok: false, fields: {}, form: 'Moved on.',
    });
  });

  it.each(['unknown', 'constructor', 'toString', '__proto__', 'icon.other'])('does not map unknown path %s', (path) => {
    expect(URGENCY_REFUSALS.field(path)).toBeNull();
  });
});

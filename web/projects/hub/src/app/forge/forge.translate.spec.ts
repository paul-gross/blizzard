import { readRefusal } from 'fleet';
import { forgeDetail } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { forge } from './forge.builders';
import { EMPTY_FORGE_DRAFT } from './forge.domain';
import {
  FORGE_REFUSALS,
  fromForgeDetail,
  fromForgeSummary,
  toForgeCreateRequest,
  toForgeDraft,
  toForgeEditBaseline,
  toForgeEditRequest,
} from './forge.translate';

const DRAFT = { slug: 'ghe', name: 'Enterprise', kind: 'github', apiBase: 'https://ghe.test/api/v3', webBase: '' };

describe('fromForgeSummary and fromForgeDetail', () => {
  it('map a summary to the domain, a null web base staying null', () => {
    const wire = forgeDetail({ forge_id: 'frg_1', revision: 2, web_base: null });
    expect(fromForgeSummary(wire)).toEqual({
      id: 'frg_1', slug: 'github', name: 'GitHub', kind: 'github', apiBase: 'https://api.github.com',
      webBase: null, webOrigin: 'https://github.com', revision: 2, retired: false,
    });
  });

  it('map a detail with budgets with and without a secret, and both reference kinds', () => {
    const record = fromForgeDetail(
      forgeDetail({
        created_by_name: 'Ana',
        offered: ['merge'],
        budgets: [
          { bucket: 'core', secret_name: 'gh', authority: 'secret/gh', remaining: 1, limit: 2, reset: 'r' },
          { bucket: 'graphql', secret_name: null, authority: null, remaining: 3, limit: 4, reset: 's' },
        ],
        repositories: [{ kind: 'repository', key: 'repo_1', label: 'blizzard' }],
        work_sources: [{ kind: 'work_source', key: 'wsrc_1', label: null }],
      }),
    );
    expect(record).toMatchObject({
      createdBy: 'usr_1', createdByName: 'Ana', offered: ['merge'],
      budgets: [
        { bucket: 'core', secretName: 'gh', remaining: 1, limit: 2, reset: 'r' },
        { bucket: 'graphql', secretName: null, remaining: 3, limit: 4, reset: 's' },
      ],
      repositories: [{ key: 'repo_1', label: 'blizzard' }],
      workSources: [{ key: 'wsrc_1', label: null }],
    });
  });
});

describe('the edit baseline', () => {
  it('carries the id, the revision, and a draft with a null web base as blank', () => {
    const baseline = toForgeEditBaseline(forge({ revision: 4 }));
    expect(baseline).toEqual({
      id: 'frg_1', revision: 4,
      draft: { slug: 'github', name: 'GitHub', kind: 'github', apiBase: 'https://api.github.com', webBase: '' },
    });
    expect(toForgeDraft(forge({ webBase: 'https://w.test' })).webBase).toBe('https://w.test');
  });
});

describe('toForgeCreateRequest', () => {
  it('trims every field, keeps the kind, and omits a blank name or web base', () => {
    expect(toForgeCreateRequest({ ...EMPTY_FORGE_DRAFT, slug: ' ghe ', kind: ' github ', apiBase: ' https://a.test ', name: '  ' }))
      .toEqual({ slug: 'ghe', kind: 'github', api_base: 'https://a.test' });
    expect(toForgeCreateRequest({ ...DRAFT, name: ' E ', webBase: ' https://w.test ' }))
      .toEqual({ slug: 'ghe', kind: 'github', api_base: 'https://ghe.test/api/v3', name: 'E', web_base: 'https://w.test' });
  });
});

describe('toForgeEditRequest', () => {
  it('sends nothing when nothing changed, trimmed text comparing equal', () => {
    expect(toForgeEditRequest(DRAFT, { ...DRAFT, name: ' Enterprise ' })).toEqual({});
  });

  it('sends a changed name alone', () => {
    expect(toForgeEditRequest(DRAFT, { ...DRAFT, name: 'Other' })).toEqual({ name: 'Other' });
  });

  it('sends a cleared web base as null and a changed API base as itself', () => {
    const before = { ...DRAFT, webBase: 'https://w.test' };
    expect(toForgeEditRequest(before, { ...before, webBase: '' })).toEqual({ web_base: null });
    expect(toForgeEditRequest(DRAFT, { ...DRAFT, apiBase: 'https://b.test' })).toEqual({ api_base: 'https://b.test' });
  });

  it('never sends the kind', () => {
    expect(toForgeEditRequest(DRAFT, { ...DRAFT, kind: 'gitlab' })).toEqual({});
  });
});

describe('FORGE_REFUSALS through readRefusal', () => {
  it('lands a 422 api_base entry on the API base', () => {
    const error = { detail: [{ loc: ['body', 'api_base'], msg: 'not a URL', type: 'value_error' }] };
    expect(readRefusal(error, FORGE_REFUSALS)).toEqual({ ok: false, fields: { apiBase: 'not a URL' }, form: null });
  });

  it('lands slug-taken on the slug and origin-taken on the API base', () => {
    expect(readRefusal({ type: 'urn:blizzard:problem:forge-slug-taken', detail: 'taken' }, FORGE_REFUSALS))
      .toEqual({ ok: false, fields: { slug: 'taken' }, form: null });
    expect(readRefusal({ type: 'urn:blizzard:problem:forge-origin-taken', detail: 'in use' }, FORGE_REFUSALS))
      .toEqual({ ok: false, fields: { apiBase: 'in use' }, form: null });
  });

  it('sends a revision conflict and a 5xx to the form line', () => {
    expect(readRefusal({ type: 'urn:blizzard:problem:forge-revision-conflict', detail: { message: 'changed since read' } }, FORGE_REFUSALS))
      .toEqual({ ok: false, fields: {}, form: 'changed since read' });
    expect(readRefusal({ detail: 'boom' }, FORGE_REFUSALS)).toMatchObject({ ok: false, fields: {}, form: expect.any(String) });
  });
});

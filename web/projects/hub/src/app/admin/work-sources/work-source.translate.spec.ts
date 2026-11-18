import { workSourceSummary } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { workSource } from './work-source.builders';
import type { WorkSourceDraft } from './work-source.domain';
import {
  fromWorkSourceSummary,
  toDraft,
  toCreateRequest,
  toEditBaseline,
  toEditRequest,
  WORK_SOURCE_REFUSALS,
} from './work-source.translate';

const DRAFT: WorkSourceDraft = {
  slug: 'winter',
  name: '',
  provider: 'github',
  locator: 'paul-gross/winter',
  forge: 'frg_github',
  credential: '',
  annotate: false,
};

describe('fromWorkSourceSummary', () => {
  it('reads a served work source into the domain', () => {
    expect(
      fromWorkSourceSummary(
        workSourceSummary({ work_source_id: 'wsrc_w', slug: 'winter', forge_id: 'frg_github', credential_id: 'cred_w', revision: 2 }),
      ),
    ).toMatchObject({ id: 'wsrc_w', slug: 'winter', forgeId: 'frg_github', credentialId: 'cred_w', revision: 2, builtIn: false });
  });

  it('reads the built-in hub source, whose id and revision are null', () => {
    const hub = fromWorkSourceSummary(
      workSourceSummary({ name: 'hub', slug: null, work_source_id: null, revision: null, built_in: true }),
    );
    expect(hub).toMatchObject({ id: null, revision: null, slug: null, name: 'hub', builtIn: true });
  });
});

describe('toEditBaseline', () => {
  it('captures the id, revision, and a draft with the forge and credential as ids', () => {
    const baseline = toEditBaseline(workSource({ id: 'wsrc_w', revision: 4, forgeId: 'frg_github', credentialId: 'cred_w', locator: 'a/b' }));
    expect(baseline).toEqual({
      id: 'wsrc_w',
      revision: 4,
      draft: { slug: 'jira', name: 'jira', provider: 'jira', locator: 'a/b', forge: 'frg_github', credential: 'cred_w', annotate: false },
    });
  });

  it('is none for a source with no id or revision', () => {
    expect(toEditBaseline(workSource({ id: null, revision: null }))).toBeNull();
  });

  it('opens blank where the source has no value', () => {
    expect(toDraft(workSource({ locator: null, forgeId: null, provider: null }))).toMatchObject({ locator: '', forge: '', provider: '' });
  });
});

describe('toCreateRequest', () => {
  it('trims, omits a blank name and credential, and always sends annotate', () => {
    expect(toCreateRequest({ ...DRAFT, slug: ' winter ', locator: ' paul-gross/winter ' })).toEqual({
      slug: 'winter',
      provider: 'github',
      locator: 'paul-gross/winter',
      forge: 'frg_github',
      annotate: false,
    });
  });

  it('sends a stated name and credential and a checked annotate', () => {
    expect(toCreateRequest({ ...DRAFT, name: ' Winter ', credential: 'cred_w', annotate: true })).toMatchObject({
      name: 'Winter',
      credential: 'cred_w',
      annotate: true,
    });
  });
});

describe('toEditRequest', () => {
  it('is sparse: only fields whose trimmed value changed', () => {
    expect(toEditRequest(DRAFT, { ...DRAFT })).toEqual({});
    expect(toEditRequest(DRAFT, { ...DRAFT, slug: ' winter ' })).toEqual({});
    expect(toEditRequest(DRAFT, { ...DRAFT, name: 'Winter' })).toEqual({ name: 'Winter' });
    expect(toEditRequest(DRAFT, { ...DRAFT, forge: 'frg_ghe', credential: 'cred_e', annotate: true })).toEqual({
      forge: 'frg_ghe',
      credential: 'cred_e',
      annotate: true,
    });
  });

  it('sends null for a cleared text field and blank for a cleared choice', () => {
    const before = { ...DRAFT, name: 'Winter', credential: 'cred_w' };
    expect(toEditRequest(before, { ...before, name: '  ', credential: '' })).toEqual({ name: null, credential: '' });
  });

  it('never carries the revision', () => {
    expect(JSON.stringify(toEditRequest(DRAFT, { ...DRAFT, name: 'x' }))).not.toContain('revision');
  });
});

describe('WORK_SOURCE_REFUSALS', () => {
  it('maps a body field to the draft field of the same name, and the deprecated secret to none', () => {
    expect(WORK_SOURCE_REFUSALS.field('locator')).toBe('locator');
    expect(WORK_SOURCE_REFUSALS.field('forge')).toBe('forge');
    expect(WORK_SOURCE_REFUSALS.field('secret')).toBeNull();
  });

  it('sends a taken slug or locator to its field, and a revision conflict to none', () => {
    expect(WORK_SOURCE_REFUSALS.kinds['urn:blizzard:problem:work-source-slug-taken']).toBe('slug');
    expect(WORK_SOURCE_REFUSALS.kinds['urn:blizzard:problem:work-source-locator-taken']).toBe('locator');
    expect(Object.hasOwn(WORK_SOURCE_REFUSALS.kinds, 'urn:blizzard:problem:revision-conflict')).toBe(false);
  });
});

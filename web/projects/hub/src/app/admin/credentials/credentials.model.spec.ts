import type { CredentialBindingView, ForgeBindingsView, ForgeSummary } from 'fleet';
import { credentialDocument, credentialSummary } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { lifecycleTone } from '../../core/lifecycle-tone';
import { configEdit, createBody, formValuesOf, shownFields } from '../config-edit.model';
import {
  bindingOptions,
  credentialCreate,
  credentialFields,
  credentialOptions,
  credentialPatch,
  credentialPickerFields,
  credentialRecordVm,
  credentialRows,
  credentialShown,
  settingField,
} from './credentials.model';

const GITHUB: ForgeSummary = {
  forge_id: 'frg_g', slug: 'github', name: 'GitHub', kind: 'github', api_base: 'https://api.github.com',
  web_base: null, web_origin: 'https://github.com', revision: 1, retired: false,
  created_at: '2026-01-01T00:00:00Z', created_by: 'usr_1', created_by_name: null,
};
const GITLAB: ForgeSummary = { ...GITHUB, forge_id: 'frg_l', slug: 'gitlab', name: 'GitLab', kind: 'gitlab' };
const TOKEN: CredentialBindingView = { name: 'github.token', forge_kind: 'github', settings: [{ key: 'variant', choices: ['classic', 'fine-grained'] }] };
const LAB: CredentialBindingView = { name: 'gitlab.token', forge_kind: 'gitlab', settings: [] };
const BINDINGS = [TOKEN, LAB];
const FORGE_BINDINGS: ForgeBindingsView[] = [{ forge_id: 'frg_g', bindings: ['github.token'] }, { forge_id: 'frg_l', bindings: ['gitlab.token'] }];
const VARIANT = settingField('github.token', 'variant');

describe('credential rows', () => {
  it('shows the slug, forge, binding and how many records name it, and filters lifecycle', () => {
    const rows = credentialRows([
      credentialSummary({ referrer_count: 2 }),
      credentialSummary({ credential_id: 'cred_o', slug: 'old', name: 'Old', referrer_count: 1, retired: true }),
    ], 'all');
    expect(rows[0]).toMatchObject({ key: 'cred_g', title: 'gh-main', sub: ['github', 'github.token', 'used by 2 records'], retired: false });
    expect(rows[1]).toMatchObject({ sub: ['Old', 'github', 'github.token', 'used by 1 record'], badges: [{ label: 'retired', tone: lifecycleTone(true) }] });
    expect(credentialRows([credentialSummary({ retired: true })], 'active')).toEqual([]);
  });
});

describe('credential form', () => {
  it('offers only the bindings the hub allows on the picked forge, keyed by forge', () => {
    expect(bindingOptions(FORGE_BINDINGS)).toEqual([
      { value: 'github.token', label: 'github.token', kind: 'frg_g' },
      { value: 'gitlab.token', label: 'gitlab.token', kind: 'frg_l' },
    ]);
    const binding = credentialFields([GITHUB, GITLAB], BINDINGS, FORGE_BINDINGS).find((field) => field.key === 'binding')!;
    expect(binding).toMatchObject({ filterBy: 'forge', createOnly: true });
  });

  it('shows a binding\'s settings only while it is picked', () => {
    const fields = credentialFields([GITHUB], BINDINGS, FORGE_BINDINGS);
    expect(shownFields(fields, { binding: 'github.token' }).map((field) => field.key)).toContain(VARIANT);
    expect(shownFields(fields, { binding: 'gitlab.token' }).map((field) => field.key)).not.toContain(VARIANT);
  });

  it('creates with the picked binding\'s settings, leaving empty ones and an empty name out', () => {
    const fields = credentialFields([GITHUB], BINDINGS, FORGE_BINDINGS);
    const form = { slug: 'gh-main', name: '', forge: 'frg_g', binding: 'github.token', secret: 'gh-token', [VARIANT]: 'classic' };
    expect(credentialCreate(createBody(fields, form), BINDINGS)).toEqual({
      slug: 'gh-main', forge: 'frg_g', binding: 'github.token', secret: 'gh-token', settings: { variant: 'classic' },
    });
    expect(credentialCreate(createBody(fields, { ...form, [VARIANT]: '' }), BINDINGS)).not.toHaveProperty('settings');
  });

  it('edits only what changed, sending the whole settings when a setting moves', () => {
    const fields = credentialFields([GITHUB], BINDINGS, FORGE_BINDINGS);
    const record = credentialSummary({ settings: { variant: 'classic' } });
    const shown = formValuesOf(fields, credentialShown(record));
    expect(shown).toMatchObject({ forge: 'frg_g', binding: 'github.token', secret: 'gh-token', [VARIANT]: 'classic' });
    const moved = { ...shown, [VARIANT]: 'fine-grained' };
    expect(credentialPatch(configEdit(fields, shown, moved).body, moved, record.binding, BINDINGS)).toEqual({ settings: { variant: 'fine-grained' } });
    const renamed = { ...shown, secret: 'gh-other' };
    expect(credentialPatch(configEdit(fields, shown, renamed).body, renamed, record.binding, BINDINGS)).toEqual({ secret: 'gh-other' });
  });
});

describe('credential detail', () => {
  it('shows the authority as a fact, its settings, and links each referrer', () => {
    const vm = credentialRecordVm(credentialDocument({
      settings: { variant: 'classic' },
      referrers: [
        { kind: 'repository', key: 'repo_c', label: 'core' },
        { kind: 'work_source', key: 'wsrc_j', label: 'issues' },
      ],
    }))!;
    const facts = vm.facts.map((fact) => [fact.label, fact.value]);
    expect(facts).toContainEqual(['Authority', 'secret/gh-token']);
    expect(facts).toContainEqual(['variant', 'classic']);
    expect(vm.links?.links).toEqual([
      { kind: 'repository', name: 'core', route: ['/admin', 'repositories', 'repo_c'] },
      { kind: 'work source', name: 'issues', route: ['/admin', 'work-sources', 'wsrc_j'] },
    ]);
    expect(credentialRecordVm(undefined)).toBeNull();
  });

  it('never offers the authority as a form field', () => {
    expect(credentialFields([GITHUB], BINDINGS, FORGE_BINDINGS).map((field) => field.key)).not.toContain('authority');
  });
});

describe('credential picker', () => {
  it('offers each enabled credential keyed by its forge', () => {
    const options = credentialOptions([
      credentialSummary(),
      credentialSummary({ credential_id: 'cred_l', slug: 'lab', forge_id: 'frg_l', binding: 'gitlab.token' }),
      credentialSummary({ credential_id: 'cred_r', retired: true }),
    ]);
    expect(options).toEqual([
      { value: 'cred_g', label: 'gh-main · github.token', kind: 'frg_g' },
      { value: 'cred_l', label: 'lab · gitlab.token', kind: 'frg_l' },
    ]);
    expect(credentialPickerFields([{ key: 'credential', label: 'Credential', kind: 'select', filterBy: 'forge' }], [credentialSummary()])[0].options)
      .toEqual([{ value: 'cred_g', label: 'gh-main · github.token', kind: 'frg_g' }]);
  });
});

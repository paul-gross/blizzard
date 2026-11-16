import type { CredentialBindingView, CredentialCheckView, ForgeBindingsView, ForgeSummary } from 'fleet';
import { credentialDocument, credentialSummary } from 'fleet/testing';
import { describe, expect, it } from 'vitest';

import { lifecycleTone } from '../../core/lifecycle-tone';
import { configEdit, createBody, formValuesOf, shownFields } from '../config-edit.model';
import {
  bindingOptions,
  checkFailure,
  checkOutcomeTone,
  credentialCheckTarget,
  isCheckFor,
  shownCheckVm,
  credentialCheckVm,
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
const TOKEN: CredentialBindingView = { name: 'github.token', forge_kind: 'github', settings: [{ key: 'variant', kind: 'choice', choices: ['classic', 'fine-grained'] }] };
const LAB: CredentialBindingView = { name: 'gitlab.token', forge_kind: 'gitlab', settings: [] };
const APP: CredentialBindingView = {
  name: 'github.app-installation', forge_kind: 'github',
  settings: [{ key: 'app_id', kind: 'positive-integer', choices: [] }, { key: 'installation_id', kind: 'positive-integer', choices: [] }],
};
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

  it('renders an integer setting as a number field shown only with its binding, and sends it as a number', () => {
    const bindings = [TOKEN, APP];
    const forgeBindings: ForgeBindingsView[] = [{ forge_id: 'frg_g', bindings: ['github.token', 'github.app-installation'] }];
    const fields = credentialFields([GITHUB], bindings, forgeBindings);
    const appId = settingField(APP.name, 'app_id');
    const installationId = settingField(APP.name, 'installation_id');
    expect(fields.find((field) => field.key === appId)).toMatchObject({ kind: 'number', shownWhen: { key: 'binding', value: APP.name } });
    expect(shownFields(fields, { binding: 'github.token' }).map((field) => field.key)).not.toContain(appId);

    const form = { slug: 'gh-app', name: '', forge: 'frg_g', binding: APP.name, secret: 'app-key', [appId]: '12', [installationId]: ' 34 ' };
    expect(credentialCreate(createBody(fields, form), bindings).settings).toEqual({ app_id: 12, installation_id: 34 });

    const record = credentialSummary({ binding: APP.name, settings: { app_id: 12, installation_id: 34 } });
    const shown = formValuesOf(fields, credentialShown(record));
    const moved = { ...shown, [installationId]: '56' };
    expect(credentialPatch(configEdit(fields, shown, moved).body, moved, record.binding, bindings)).toEqual({ settings: { app_id: 12, installation_id: 56 } });
  });

  it('takes a setting of a kind this build does not know as text', () => {
    const odd = { key: 'region', kind: 'region-code', choices: [] } as unknown as CredentialBindingView['settings'][number];
    const fields = credentialFields([GITHUB], [{ ...TOKEN, settings: [odd] }], FORGE_BINDINGS);
    expect(fields.find((field) => field.key === settingField(TOKEN.name, 'region'))?.kind).toBe('text');
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

  describe('credentialCheckVm', () => {
    const check: CredentialCheckView = {
      credential: 'gh-main', binding: 'github.app-installation', variant: null, variant_contradicted: false, granted_permissions: { contents: 'write' },
      mint: { operation: 'mint', outcome: 'refused', detail: 'The installation is suspended.', diagnostic: null },
      not_evaluated: 'Branch protection, required reviews, and rulesets are not evaluated.',
      uses: [{
        kind: 'work_source', id: 'wsrc_i', slug: 'issues', reached: false,
        operations: [{
          operation: 'issues.read', outcome: 'refused', detail: null,
          diagnostic: {
            status: 404, endpoint: 'GET /repos/{owner}/{repo}/issues', message: 'Not Found', accepted_permissions: null, binding: null,
            credential: null, documentation_url: null, operation: 'issues.read', repository: 'acme/core', request_id: null, variant: null,
          },
        }],
      }],
    };

    it('maps the mint, each use, and a diagnostic to its status, call, and words', () => {
      const vm = credentialCheckVm(check);
      expect(vm.identity).toBe('github.app-installation');
      expect(vm.variantWarning).toBeNull();
      expect(vm.mint).toEqual({ operation: 'mint', outcome: 'refused', tone: 'needs', detail: 'The installation is suspended.', diagnostic: null });
      expect(vm.uses).toEqual([{
        key: 'work_source:wsrc_i', heading: 'work source issues', reached: false,
        operations: [{ operation: 'issues.read', outcome: 'refused', tone: 'needs', detail: null, diagnostic: '404 GET /repos/{owner}/{repo}/issues: Not Found' }],
      }]);
      expect(vm.notEvaluated).toBe(check.not_evaluated);
    });

    it('names the variant and warns when the token contradicts it', () => {
      const vm = credentialCheckVm({ ...check, binding: 'github.token', variant: 'classic', variant_contradicted: true });
      expect(vm.identity).toBe('github.token · classic');
      expect(vm.variantWarning).toContain('disagrees');
    });

    it('gives each outcome its own tone', () => {
      expect((['proven', 'granted', 'unproven', 'refused', 'unsupported'] as const).map(checkOutcomeTone)).toEqual(['done', 'spawning', 'idle', 'needs', 'waiting']);
    });
  });

  describe('credential check target', () => {
    it('is offered only to an editor, for a record naming a credential', () => {
      expect(credentialCheckTarget(true, 'cred_g')).toEqual({ credentialId: 'cred_g', scope: {} });
      expect(credentialCheckTarget(true, 'cred_g', { field: 'repositories', id: 'repo_c' })).toEqual({ credentialId: 'cred_g', scope: { repositories: ['repo_c'] } });
      expect(credentialCheckTarget(true, 'cred_g', { field: 'work_sources', id: 'wsrc_j' })).toEqual({ credentialId: 'cred_g', scope: { work_sources: ['wsrc_j'] } });
      expect(credentialCheckTarget(false, 'cred_g')).toBeNull();
      expect(credentialCheckTarget(true, null, { field: 'repositories', id: 'repo_c' })).toBeNull();
      expect(credentialCheckTarget(true, 'cred_g', { field: 'work_sources', id: null })).toBeNull();
    });

    it('shows an answer or failure only for the target it was posted for', () => {
      const target = { credentialId: 'cred_g', scope: { repositories: ['repo_c'] } };
      expect(isCheckFor({ credentialId: 'cred_g', scope: { repositories: ['repo_c'] } }, target)).toBe(true);
      expect(isCheckFor({ credentialId: 'cred_g', scope: {} }, target)).toBe(false);
      expect(isCheckFor(undefined, target)).toBe(false);
      expect(shownCheckVm(undefined, true)).toBeNull();
      expect(checkFailure(true, { detail: 'credential gh-main is not used by docs' }, true)).toBe('credential gh-main is not used by docs');
      expect(checkFailure(true, new Error('x'), false)).toBeNull();
    });
  });
});

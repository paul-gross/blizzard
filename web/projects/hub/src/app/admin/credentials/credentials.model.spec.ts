import { EMPTY_CREDENTIAL_DRAFT, withForge, type CredentialCheck, type CredentialDocument, type CredentialListItem } from './credential.domain';
import { describe, expect, it } from 'vitest';

import { lifecycleTone } from '../../core/lifecycle-tone';
import {
  credentialCreateFields,
  createSettingsUnavailable,
  removedCreateSettings,
  checkFailure,
  checkOutcomeTone,
  credentialCheckTarget,
  isCheckFor,
  shownCheckVm,
  credentialCheckVm,
  credentialOptions,
  credentialPickerFields,
  credentialRecordVm,
  credentialRows,
} from './credentials.model';

const credential = (overrides: Partial<CredentialListItem> = {}): CredentialListItem => ({
  id: 'cred_g', slug: 'gh-main', name: 'gh-main', forgeId: 'frg_g', forgeSlug: 'github', forgeName: 'GitHub',
  binding: 'github.token', secretName: 'gh-token', settings: {}, authority: 'secret/gh-token', retired: false,
  revision: 1, createdBy: 'usr_1', createdByName: null, createdAt: '2026-01-01', referrerCount: 0, ...overrides,
});
const document = (overrides: Partial<CredentialDocument> = {}): CredentialDocument => ({ ...credential(), referrers: [], ...overrides });

describe('credential rows', () => {
  it('shows the slug, forge, binding and how many records name it, and filters lifecycle', () => {
    const rows = credentialRows([
      credential({ referrerCount: 2 }),
      credential({ id: 'cred_o', slug: 'old', name: 'Old', referrerCount: 1, retired: true }),
    ], 'all');
    expect(rows[0]).toMatchObject({ key: 'cred_g', title: 'gh-main', sub: ['github', 'github.token', 'used by 2 records'], retired: false });
    expect(rows[1]).toMatchObject({ sub: ['Old', 'github', 'github.token', 'used by 1 record'], badges: [{ label: 'retired', tone: lifecycleTone(true) }] });
    expect(credentialRows([credential({ retired: true })], 'active')).toEqual([]);
  });
});

describe('credential detail', () => {
  it('shows the authority as a fact, its settings, and links each referrer', () => {
    const vm = credentialRecordVm(document({
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


});

describe('credential picker', () => {
  it('offers each enabled credential keyed by its forge', () => {
    const options = credentialOptions([
      { id: 'cred_g', slug: 'gh-main', binding: 'github.token', forgeId: 'frg_g', retired: false },
      { id: 'cred_l', slug: 'lab', binding: 'gitlab.token', forgeId: 'frg_l', retired: false },
      { id: 'cred_r', slug: 'old', binding: 'github.token', forgeId: 'frg_g', retired: true },
    ]);
    expect(options).toEqual([
      { value: 'cred_g', label: 'gh-main · github.token', kind: 'frg_g' },
      { value: 'cred_l', label: 'lab · gitlab.token', kind: 'frg_l' },
    ]);
    expect(credentialPickerFields([{ key: 'credential', label: 'Credential', kind: 'select', filterBy: 'forge' }], [{ id: 'cred_g', slug: 'gh-main', binding: 'github.token', forgeId: 'frg_g', retired: false }])[0].options)
      .toEqual([{ value: 'cred_g', label: 'gh-main · github.token', kind: 'frg_g' }]);
  });

  describe('credentialCheckVm', () => {
    const check: CredentialCheck = {
      credential: 'gh-main', binding: 'github.app-installation', variant: null, variantContradicted: false, grantedPermissions: { contents: 'write' },
      mint: { operation: 'mint', outcome: 'refused', detail: 'The installation is suspended.', diagnostic: null },
      notEvaluated: 'Branch protection, required reviews, and rulesets are not evaluated.',
      unreachedReason: null,
      uses: [{
        kind: 'work_source', id: 'wsrc_i', slug: 'issues', reached: false,
        operations: [{
          operation: 'issues.read', outcome: 'refused', detail: null,
          diagnostic: {
            status: 404, endpoint: 'GET /repos/{owner}/{repo}/issues', message: 'Not Found',
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
      expect(vm.notEvaluated).toBe(check.notEvaluated);
      expect(vm.unreachedReason).toBeNull();
    });

    it('says why no use was reached, and heads a use of a kind this build does not know as sent', () => {
      const odd = { ...check.uses[0], kind: { unlisted: 'mailing_list' } };
      const vm = credentialCheckVm({ ...check, unreachedReason: 'the installation could not be read: ReadTimeout', uses: [odd] });
      expect(vm.unreachedReason).toBe('the installation could not be read: ReadTimeout');
      expect(vm.uses[0].heading).toBe('mailing_list issues');
    });

    it('names the variant and warns when the token contradicts it', () => {
      const vm = credentialCheckVm({ ...check, binding: 'github.token', variant: 'classic', variantContradicted: true });
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

describe('credential create choices', () => {
  it('identifies unavailable schemas and entered settings removed from the selected binding', () => {
    const type = { kind: 'unknown' as const, raw: 'text' };
    const bindings = [{ name: 'token', forgeKind: 'github', settings: [{ key: 'kept', type }] }];
    const draft = { ...EMPTY_CREDENTIAL_DRAFT, binding: 'token', settings: [
      { binding: 'token', key: 'kept', type, text: 'value' },
      { binding: 'token', key: 'removed', type, text: 'value' },
      { binding: 'token', key: 'empty', type, text: '  ' },
      { binding: 'other', key: 'other', type, text: 'value' },
    ] };
    expect(createSettingsUnavailable(draft, bindings)).toBe(false);
    expect(removedCreateSettings(draft, bindings)).toEqual(['removed']);
    expect(createSettingsUnavailable(draft, [])).toBe(true);
    expect(removedCreateSettings(draft, [])).toEqual([]);
    expect(createSettingsUnavailable({ ...draft, binding: '' }, [])).toBe(false);
    expect(removedCreateSettings(draft, [{ ...bindings[0], settings: [...bindings[0].settings, { key: 'removed', type }] }])).toEqual([]);
    expect(removedCreateSettings({ ...draft, binding: 'other' }, [{ name: 'other', forgeKind: 'gitlab', settings: [] }])).toEqual(['other']);
  });

  it('offers active forges and associates binding choices with their forge', () => {
    const fields = credentialCreateFields(
      [{ id: 'a', slug: 'alpha', name: 'Alpha', retired: false }, { id: 'b', slug: 'old', name: 'Old', retired: true }],
      [{ forgeId: 'a', bindings: ['token', 'app'] }, { forgeId: 'b', bindings: ['token'] }],
    );
    expect(fields.find((field) => field.key === 'forge')?.options).toEqual([{ value: 'a', label: 'alpha' }]);
    expect(fields.find((field) => field.key === 'binding')?.options).toEqual([
      { value: 'token', label: 'token', kind: 'a' }, { value: 'app', label: 'app', kind: 'a' },
      { value: 'token', label: 'token', kind: 'b' },
    ]);
  });

  it('preserves a compatible binding and clears an incompatible one without losing setting drafts', () => {
    const draft = { ...EMPTY_CREDENTIAL_DRAFT, forge: 'a', binding: 'token', settings: [{ binding: 'token', key: 'variant', type: { kind: 'unknown' as const, raw: 'x' }, text: 'saved' }] };
    const choices = [{ forgeId: 'b', bindings: ['token'] }, { forgeId: 'c', bindings: ['app'] }];
    expect(withForge(draft, 'b', choices)).toMatchObject({ forge: 'b', binding: 'token', settings: draft.settings });
    expect(withForge(draft, 'c', choices)).toMatchObject({ forge: 'c', binding: '', settings: draft.settings });
  });
});

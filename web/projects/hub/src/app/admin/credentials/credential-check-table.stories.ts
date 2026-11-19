import type { Meta, StoryObj } from '@storybook/angular-vite';
import type { CredentialCheck, CredentialCheckOperation, CredentialCheckUse } from './credential.domain';

import { CredentialCheckTable } from './credential-check-table';
import { credentialCheckVm } from './credentials.model';

const NOT_EVALUATED = 'Branch protection, required reviews, and rulesets are not evaluated.';

const op = (operation: string, outcome: CredentialCheckOperation['outcome'], detail: string | null = null): CredentialCheckOperation => ({
  operation, outcome, detail, diagnostic: null,
});

const repo = (slug: string, operations: CredentialCheckOperation[], reached = true): CredentialCheckUse => ({
  kind: 'repository', id: `repo_${slug}`, slug, operations, reached,
});

const check = (overrides: Partial<CredentialCheck> = {}): CredentialCheck => ({
  credential: 'gh-main', binding: 'github.token', variant: 'classic', variantContradicted: false,
  grantedPermissions: null, mint: null, notEvaluated: NOT_EVALUATED, unreachedReason: null,
  uses: [
    repo('core', [
      op('access', 'proven'),
      op('refs.read', 'proven'),
      op('checks.read', 'proven', '1 check run read — the read is proven, not that checks run.'),
      op('changes.merge.squash', 'granted', 'The token carries the repo scope.'),
    ]),
  ],
  ...overrides,
});

const refusedRead: CredentialCheckOperation = {
  operation: 'checks.read', outcome: 'refused',
  detail: 'A fine-grained token cannot read check runs on a private repository. Use a classic token with the repo scope.',
  diagnostic: {
    status: 403, endpoint: 'GET /repos/{owner}/{repo}/commits/{ref}/check-runs', message: 'Resource not accessible by personal access token',
  },
};

const meta: Meta<CredentialCheckTable> = {
  title: 'Hub admin/Credential check table',
  component: CredentialCheckTable,
  args: { vm: credentialCheckVm(check()) },
};
export default meta;
type Story = StoryObj<CredentialCheckTable>;

export const Proven: Story = {};
export const EachOutcome: Story = {
  args: {
    vm: credentialCheckVm(check({
      variant: 'fine-grained',
      uses: [
        repo('core', [
          op('access', 'proven'),
          op('changes.merge.squash', 'unproven', 'A fine-grained token does not say what it grants; the write is not shown.'),
          op('checks.rerun', 'unsupported', 'The binding does not offer this capability.'),
          refusedRead,
        ]),
        { kind: 'work_source', id: 'wsrc_i', slug: 'issues', reached: true, operations: [op('issues.read', 'proven'), op('issues.label', 'granted')] },
      ],
    })),
  },
};
export const RefusedMint: Story = {
  args: {
    vm: credentialCheckVm(check({
      binding: 'github.app-installation', variant: null,
      mint: op('mint', 'refused', 'The installation is suspended. Unsuspend it in the organization settings.'),
      uses: [repo('core', [])],
    })),
  },
};
export const UnreachedUse: Story = {
  args: {
    vm: credentialCheckVm(check({
      uses: [repo('core', [op('access', 'proven'), op('refs.read', 'proven')]), repo('docs', [], false)],
    })),
  },
};
export const NoUseReached: Story = {
  args: {
    vm: credentialCheckVm(check({
      binding: 'github.app-installation', variant: null,
      unreachedReason: 'the installation could not be read: ReadTimeout',
      uses: [repo('core', [], false), repo('docs', [], false)],
    })),
  },
};
export const VariantContradicted: Story = { args: { vm: credentialCheckVm(check({ variant: 'fine-grained', variantContradicted: true })) } };
export const NoUses: Story = { args: { vm: credentialCheckVm(check({ uses: [] })) } };
export const PhoneSmall: Story = { ...EachOutcome, globals: { viewport: { value: 'phone-sm', isRotated: false } } };

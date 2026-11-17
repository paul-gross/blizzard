import type { Meta, StoryObj } from '@storybook/angular-vite';
import type { CredentialCheckView, OperationResultView, UseResultView } from 'fleet';

import { CredentialCheckTable } from './credential-check-table';
import { credentialCheckVm } from './credentials.model';

const NOT_EVALUATED = 'Branch protection, required reviews, and rulesets are not evaluated.';

const op = (operation: string, outcome: OperationResultView['outcome'], detail: string | null = null): OperationResultView => ({
  operation, outcome, detail, diagnostic: null,
});

const repo = (slug: string, operations: OperationResultView[], reached = true): UseResultView => ({
  kind: 'repository', id: `repo_${slug}`, slug, operations, reached,
});

const check = (overrides: Partial<CredentialCheckView> = {}): CredentialCheckView => ({
  credential: 'gh-main', binding: 'github.token', variant: 'classic', variant_contradicted: false,
  granted_permissions: null, mint: null, not_evaluated: NOT_EVALUATED, unreached_reason: null,
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

const refusedRead: OperationResultView = {
  operation: 'checks.read', outcome: 'refused',
  detail: 'A fine-grained token cannot read check runs on a private repository. Use a classic token with the repo scope.',
  diagnostic: {
    status: 403, endpoint: 'GET /repos/{owner}/{repo}/commits/{ref}/check-runs', message: 'Resource not accessible by personal access token',
    accepted_permissions: null, binding: 'github.token', credential: 'gh-main', documentation_url: null, operation: 'checks.read',
    repository: 'acme/core', request_id: null, variant: 'fine-grained',
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
      unreached_reason: 'the installation could not be read: ReadTimeout',
      uses: [repo('core', [], false), repo('docs', [], false)],
    })),
  },
};
export const VariantContradicted: Story = { args: { vm: credentialCheckVm(check({ variant: 'fine-grained', variant_contradicted: true })) } };
export const NoUses: Story = { args: { vm: credentialCheckVm(check({ uses: [] })) } };
export const PhoneSmall: Story = { ...EachOutcome, globals: { viewport: { value: 'phone-sm', isRotated: false } } };

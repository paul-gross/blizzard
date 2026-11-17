import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { CredentialCheckView, OperationResultView } from 'fleet';

import { CredentialCheckTable } from './credential-check-table';
import { credentialCheckVm } from './credentials.model';

const NOT_EVALUATED = 'Branch protection, required reviews, and rulesets are not evaluated.';
const op = (operation: string, outcome: OperationResultView['outcome'], detail: string | null = null): OperationResultView => ({
  operation, outcome, detail, diagnostic: null,
});
const CHECK: CredentialCheckView = {
  credential: 'gh-main', binding: 'github.token', variant: 'fine-grained', variant_contradicted: false,
  granted_permissions: null, mint: null, not_evaluated: NOT_EVALUATED, unreached_reason: null,
  uses: [
    {
      kind: 'repository', id: 'repo_c', slug: 'core', reached: true,
      operations: [
        op('access', 'proven'),
        op('changes.open', 'granted'),
        op('changes.merge.squash', 'unproven'),
        op('checks.rerun', 'unsupported'),
        {
          operation: 'checks.read', outcome: 'refused', detail: 'Use a classic token with the repo scope.',
          diagnostic: {
            status: 403, endpoint: 'GET /repos/{owner}/{repo}/commits/{ref}/check-runs', message: 'Resource not accessible',
            accepted_permissions: null, binding: null, credential: null, documentation_url: null, operation: 'checks.read',
            repository: 'acme/core', request_id: null, variant: null,
          },
        },
      ],
    },
    { kind: 'work_source', id: 'wsrc_i', slug: 'issues', reached: false, operations: [] },
  ],
};

describe('CredentialCheckTable', () => {
  beforeEach(() => TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] }));

  function render(check: CredentialCheckView): HTMLElement {
    const fixture = TestBed.createComponent(CredentialCheckTable);
    fixture.componentRef.setInput('vm', credentialCheckVm(check));
    fixture.detectChanges();
    return fixture.nativeElement as HTMLElement;
  }
  const all = (el: HTMLElement, id: string) => Array.from(el.querySelectorAll<HTMLElement>(`[data-testid="${id}"]`));

  it('renders each of the five outcomes, with the refusal\'s remedy and diagnostic', () => {
    const el = render(CHECK);
    const rows = all(el, 'credential-check-operation');
    expect(rows.map((row) => [row.dataset['operation'], row.dataset['outcome']])).toEqual([
      ['access', 'proven'], ['changes.open', 'granted'], ['changes.merge.squash', 'unproven'], ['checks.rerun', 'unsupported'], ['checks.read', 'refused'],
    ]);
    expect(rows[4].textContent).toContain('Use a classic token with the repo scope.');
    expect(rows[4].textContent).toContain('403 GET /repos/{owner}/{repo}/commits/{ref}/check-runs: Resource not accessible');
  });

  it('marks an unreached use and heads each use with its kind and slug', () => {
    const el = render(CHECK);
    const uses = all(el, 'credential-check-use');
    expect(uses.map((use) => use.textContent)).toEqual([expect.stringContaining('repository core'), expect.stringContaining('work source issues')]);
    expect(uses[0].querySelector('[data-testid="credential-check-unreached"]')).toBeNull();
    expect(uses[1].querySelector('[data-testid="credential-check-unreached"]')).not.toBeNull();
  });

  it('says why no use was reached, and says nothing when one was', () => {
    const el = render({ ...CHECK, unreached_reason: 'the installation could not be read: ReadTimeout' });
    expect(all(el, 'credential-check-unreached-reason')[0].textContent).toContain('No use was reached: the installation could not be read: ReadTimeout');
    expect(all(render(CHECK), 'credential-check-unreached-reason')).toHaveLength(0);
  });

  it('shows a refused mint above the uses', () => {
    const el = render({ ...CHECK, mint: op('mint', 'refused', 'The installation is suspended.'), uses: [] });
    const mint = all(el, 'credential-check-mint')[0];
    expect(mint.querySelector('[data-outcome="refused"]')?.textContent).toContain('The installation is suspended.');
    expect(all(el, 'credential-check-empty')).toHaveLength(1);
  });

  it('carries the identity, the variant warning, and the not-evaluated sentence', () => {
    const el = render({ ...CHECK, variant_contradicted: true });
    expect(all(el, 'credential-check-identity')[0].textContent).toContain('github.token · fine-grained');
    expect(all(el, 'credential-check-variant-warning')).toHaveLength(1);
    expect(all(el, 'credential-check-not-evaluated')[0].textContent).toContain(NOT_EVALUATED);
    expect(all(render(CHECK), 'credential-check-variant-warning')).toHaveLength(0);
  });
});

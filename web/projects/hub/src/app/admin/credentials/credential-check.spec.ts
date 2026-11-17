import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import type { CredentialCheckRequest, CredentialCheckView } from 'fleet';
import { type RequestClientStub, settle, stubError, stubHubClient } from 'fleet/testing';

import { CredentialCheck } from './credential-check';

const ANSWER: CredentialCheckView = {
  credential: 'gh-main', binding: 'github.token', variant: 'classic', variant_contradicted: false, granted_permissions: null, mint: null,
  not_evaluated: 'Branch protection, required reviews, and rulesets are not evaluated.', unreached_reason: null,
  uses: [{ kind: 'repository', id: 'repo_c', slug: 'core', reached: true, operations: [{ operation: 'access', outcome: 'proven', detail: null, diagnostic: null }] }],
};

describe('CredentialCheck', () => {
  let stub: RequestClientStub;
  let answer: () => unknown = () => ANSWER;
  afterEach(() => {
    answer = () => ANSWER;
    stub?.restore();
  });

  async function mount(scope?: CredentialCheckRequest) {
    stub = stubHubClient({ 'POST /api/credentials/{credential}/checks': () => answer() as never });
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideTanStackQuery(new QueryClient({ defaultOptions: { mutations: { retry: false } } }))],
    });
    const fixture = TestBed.createComponent(CredentialCheck);
    fixture.componentRef.setInput('target', { credentialId: 'cred_g', scope: scope ?? {} });
    await settle(fixture);
    return { fixture, el: fixture.nativeElement as HTMLElement };
  }
  const testid = (el: HTMLElement, id: string) => el.querySelector<HTMLElement>(`[data-testid="${id}"]`);
  const button = (el: HTMLElement) => testid(el, 'credential-check-run') as HTMLButtonElement;

  for (const scope of [{}, { repositories: ['repo_c'] }, { work_sources: ['wsrc_j'] }]) {
    it(`posts the check over ${JSON.stringify(scope)} and renders the answer`, async () => {
      const { fixture, el } = await mount(scope);
      expect(testid(el, 'credential-check')).toBeNull();
      button(el).click();
      await settle(fixture);
      expect(stub.forRoute('/api/credentials/cred_g/checks', 'POST').map((request) => request.body)).toEqual([scope]);
      expect(testid(el, 'credential-check')?.textContent).toContain('repository core');
    });
  }

  it('disables the button while the check runs', async () => {
    let release!: () => void;
    const held = new Promise<void>((resolve) => (release = resolve));
    answer = () => held.then(() => ANSWER);
    const { fixture, el } = await mount();
    button(el).click();
    // The check is held open, so the fixture never stabilises: tick and render without waiting for it.
    await new Promise((resolve) => setTimeout(resolve, 0));
    fixture.detectChanges();
    expect(button(el).disabled).toBe(true);
    release();
    await settle(fixture);
    expect(button(el).disabled).toBe(false);
  });

  it('renders a refused call through the error line', async () => {
    answer = () => stubError(422, { type: 'credential_not_used_by', title: 'Not used by', status: 422, detail: 'repository docs does not act with gh-main', referrers: [] });
    const { fixture, el } = await mount({ repositories: ['docs'] });
    button(el).click();
    await settle(fixture);
    expect(testid(el, 'credential-check-error')?.textContent).toContain('repository docs does not act with gh-main');
    expect(testid(el, 'credential-check')).toBeNull();
  });

  it('drops an answer for another credential', async () => {
    const { fixture, el } = await mount();
    button(el).click();
    await settle(fixture);
    fixture.componentRef.setInput('target', { credentialId: 'cred_b', scope: {} });
    await settle(fixture);
    expect(testid(el, 'credential-check')).toBeNull();
  });
});

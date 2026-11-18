import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import {
  credentialSummary,
  forgeDetail,
  OPERATOR_ME_RESPONSE,
  type RequestClientStub,
  settle,
  stubError,
  stubHubClient,
  workSourceSummary,
} from 'fleet/testing';

import { WorkSourcesPage } from './work-sources-page';

const EDITOR = { ...OPERATOR_ME_RESPONSE, permissions: [...OPERATOR_ME_RESPONSE.permissions, 'config:edit' as const] };

describe('WorkSourcesPage create', () => {
  let stub: RequestClientStub;
  afterEach(() => {
    stub?.restore();
    document.querySelectorAll('dialog').forEach((d) => d.remove());
  });

  async function open(post: () => unknown = () => workSourceSummary({ slug: 'winter' })) {
    stub = stubHubClient({
      'GET /api/me': () => EDITOR,
      'GET /api/work-sources': () => ({ sources: [] }),
      'POST /api/work-sources': post as never,
      'GET /api/forges': () => ({
        forges: [forgeDetail({ forge_id: 'frg_github' }), forgeDetail({ forge_id: 'frg_ghe', slug: 'ghe', name: 'Enterprise' })],
      }),
      'GET /api/credentials': () => ({
        credentials: [
          credentialSummary({ credential_id: 'cred_g', slug: 'gh-main', forge_id: 'frg_github' }),
          credentialSummary({ credential_id: 'cred_e', slug: 'ghe-main', forge_id: 'frg_ghe' }),
        ],
        bindings: [],
        forge_bindings: [],
      }),
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideRouter([{ path: 'admin/work-sources', component: WorkSourcesPage, children: [{ path: ':key', children: [] }] }]),
      ],
    });
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl('/admin/work-sources');
    await settle(harness.fixture);
    const root = harness.routeNativeElement as HTMLElement;
    const q = (id: string) => document.querySelector<HTMLElement>(`[data-testid="${id}"]`);
    const type = async (field: string, text: string) => {
      const el = q(`admin-work-sources-create-field-${field}`)!;
      const input = el instanceof HTMLInputElement ? el : el.querySelector<HTMLInputElement>('input')!;
      input.value = text;
      input.dispatchEvent(new Event('input'));
      await settle(harness.fixture);
    };
    const overlayOptions = () => [...document.querySelectorAll<HTMLElement>('.cdk-overlay-pane [role="option"]:not(fleet-kit-select-row)')];
    const pick = async (field: string, label: string) => {
      q(`admin-work-sources-create-field-${field}`)!.click();
      await settle(harness.fixture);
      overlayOptions().find((o) => o.textContent?.includes(label))!.click();
      await settle(harness.fixture);
    };
    const options = async (field: string) => {
      q(`admin-work-sources-create-field-${field}`)!.click();
      await settle(harness.fixture);
      const labels = overlayOptions().map((o) => o.textContent?.trim());
      document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
      await settle(harness.fixture);
      return labels;
    };
    const submit = async () => {
      q('kit-form-save')!.click();
      await settle(harness.fixture);
    };
    const openForm = async () => {
      root.querySelector<HTMLElement>('[data-testid="admin-work-sources-new"]')!.click();
      await settle(harness.fixture);
    };
    return { harness, root, q, type, pick, options, submit, openForm, router: TestBed.inject(Router) };
  }

  it('refuses a create with no forge, errors on Forge, and sends nothing', async () => {
    const { q, type, submit, openForm } = await open();
    await openForm();
    await type('slug', 'winter');
    await type('provider', 'github');
    await type('locator', 'paul-gross/winter');
    await submit();

    expect(stub.forRoute('/api/work-sources', 'POST')).toHaveLength(0);
    expect(q('admin-work-sources-create-dialog')!.textContent).toContain('A forge is required');
  });

  it('clears the forge and credential on a provider change, and the credential on a forge change', async () => {
    const { q, type, pick, options, openForm } = await open();
    await openForm();
    await type('provider', 'github');
    await pick('forge', 'github');
    await pick('credential', 'gh-main');
    const shown = (field: string) => q(`admin-work-sources-create-field-${field}`)!.textContent?.trim();
    expect(shown('forge')).toContain('github');
    expect(shown('credential')).toContain('gh-main');

    await type('provider', 'githubx');
    expect(shown('forge')).not.toContain('github ·');
    expect(shown('credential')).not.toContain('gh-main');

    await type('provider', 'github');
    await pick('forge', 'github ·');
    await pick('credential', 'gh-main');
    await pick('forge', 'Enterprise');
    expect(shown('credential')).not.toContain('gh-main');
    expect(await options('credential')).toEqual(['ghe-main · github.token']);
  });

  it('creates with the defaults — name left to the slug, no credential, annotate false — and opens the new source', async () => {
    const { type, pick, submit, openForm, router } = await open();
    await openForm();
    await type('slug', ' winter ');
    await type('provider', 'github');
    await type('locator', 'paul-gross/winter');
    await pick('forge', 'github');
    await submit();

    const posts = stub.forRoute('/api/work-sources', 'POST');
    expect(posts).toHaveLength(1);
    expect(posts[0].body).toEqual({
      slug: 'winter',
      provider: 'github',
      locator: 'paul-gross/winter',
      forge: 'frg_github',
      annotate: false,
    });
    expect(router.url).toBe('/admin/work-sources/winter');
  });

  it('shows a taken slug on Slug and focuses it, keeping the form open', async () => {
    const { q, type, pick, submit, openForm, router } = await open(() =>
      stubError(409, { type: 'urn:blizzard:problem:work-source-slug-taken', detail: 'slug already taken' }),
    );
    await openForm();
    await type('slug', 'winter');
    await type('provider', 'github');
    await type('locator', 'paul-gross/winter');
    await pick('forge', 'github');
    await submit();

    expect(q('admin-work-sources-create-dialog')!.textContent).toContain('slug already taken');
    expect(router.url).toBe('/admin/work-sources');
    const slug = q('admin-work-sources-create-field-slug')!;
    expect(document.activeElement === slug || slug.contains(document.activeElement)).toBe(true);
  });
});

import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { ViewportService } from 'fleet/viewport';
import { forgeDetail, OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubHubClient } from 'fleet/testing';

import { HUB_PROJECT_FILTER } from '../../core/project-filter';
import { ForgesPage } from './forges-page';

@Component({ selector: 'app-test-forges-host', imports: [RouterOutlet], template: '<router-outlet />' })
class TestForgesHost {}

@Component({ selector: 'app-test-forge-stub', template: '<p data-testid="stub-detail"></p>' })
class StubDetail {}

const refuse = (status: number, body: object): never => stubError(status, body) as never;

describe('ForgesPage create', () => {
  let stub: RequestClientStub;
  afterEach(() => {
    stub?.restore();
    document.body.innerHTML = '';
  });

  async function render(post: () => unknown = () => forgeDetail({ slug: 'ghe' })) {
    stub = stubHubClient({
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: ['config:edit'] }),
      'GET /api/forges': () => ({ forges: [forgeDetail()] }),
      'POST /api/forges': post as never,
    });
    await TestBed.configureTestingModule({
      imports: [TestForgesHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })),
        provideRouter([{ path: 'admin/forges', component: ForgesPage, children: [{ path: ':key', component: StubDetail }] }]),
        { provide: HUB_PROJECT_FILTER, useValue: signal('acme').asReadonly() },
      ],
    }).compileComponents();
    TestBed.inject(ViewportService).setOverride('desktop');
    const fixture = TestBed.createComponent(TestForgesHost);
    document.body.appendChild(fixture.nativeElement);
    await TestBed.inject(Router).navigateByUrl('/admin/forges');
    await settle(fixture, 12);
    const q = <E extends HTMLElement>(id: string) => document.querySelector<E>(`[data-testid="${id}"]`);
    const click = async (id: string) => {
      q(id)!.click();
      await settle(fixture, 12);
    };
    const type = async (id: string, value: string) => {
      const input = q<HTMLInputElement>(id)!;
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await settle(fixture, 12);
    };
    return { fixture, q, click, type, router: TestBed.inject(Router) };
  }

  const required = () => document.querySelectorAll('[data-testid="admin-forges-create-dialog"] fleet-kit-field .req').length;

  it('shows five fields with markers on slug, kind, and API base', async () => {
    const { click, q } = await render();
    await click('admin-forges-new');
    for (const key of ['slug', 'name', 'kind', 'apiBase', 'webBase']) expect(q(`admin-forges-create-field-${key}`)).not.toBeNull();
    expect(required()).toBe(3);
  });

  it('sends no POST for blanks, and focuses the slug', async () => {
    const { click, q } = await render();
    await click('admin-forges-new');
    await click('kit-form-save');
    expect(stub.forRoute('/api/forges', 'POST')).toHaveLength(0);
    expect(document.activeElement).toBe(q('admin-forges-create-field-slug'));
  });

  it('posts the trimmed sparse body and navigates to the slug', async () => {
    const { click, type, q, router } = await render();
    await click('admin-forges-new');
    await type('admin-forges-create-field-slug', ' ghe ');
    await type('admin-forges-create-field-kind', 'github');
    await type('admin-forges-create-field-apiBase', 'https://ghe.example.test/api/v3');
    await click('kit-form-save');
    expect(stub.forRoute('/api/forges', 'POST')[0].body).toEqual({
      slug: 'ghe', kind: 'github', api_base: 'https://ghe.example.test/api/v3',
    });
    expect(router.url).toBe('/admin/forges/ghe');
    expect(q('admin-forges-create-dialog')).toBeNull();
  });

  it('lands a slug-taken refusal on the slug, keeping the dialog and the draft', async () => {
    const { click, type, q } = await render(() =>
      refuse(409, { type: 'urn:blizzard:problem:forge-slug-taken', detail: 'slug ghe is taken' }),
    );
    await click('admin-forges-new');
    await type('admin-forges-create-field-slug', 'ghe');
    await type('admin-forges-create-field-kind', 'github');
    await type('admin-forges-create-field-apiBase', 'https://a.test');
    await click('kit-form-save');
    expect(q('admin-forges-create-dialog')).not.toBeNull();
    expect(q('admin-forges-create-field-slug')!.closest('fleet-kit-field')!.textContent).toContain('slug ghe is taken');
    expect(q<HTMLInputElement>('admin-forges-create-field-apiBase')!.value).toBe('https://a.test');
  });

  it('lands an api_base refusal on the API base', async () => {
    const { click, type, q } = await render(() =>
      refuse(422, { detail: [{ loc: ['body', 'api_base'], msg: 'not an origin', type: 'value_error' }] }),
    );
    await click('admin-forges-new');
    await type('admin-forges-create-field-slug', 'ghe');
    await type('admin-forges-create-field-kind', 'github');
    await type('admin-forges-create-field-apiBase', 'notaurl');
    await click('kit-form-save');
    expect(q('admin-forges-create-dialog')).not.toBeNull();
    expect(q('admin-forges-create-field-apiBase')!.closest('fleet-kit-field')!.textContent).toContain('not an origin');
  });
});

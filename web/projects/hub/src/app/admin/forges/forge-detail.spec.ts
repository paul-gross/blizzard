import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, Router, RouterOutlet } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { ViewportService } from 'fleet';
import { forgeDetail, OPERATOR_ME_RESPONSE, type RequestClientStub, settle, stubError, stubHubClient } from 'fleet/testing';

import { HUB_PROJECT_FILTER } from '../../core/project-filter';
import { ForgeDetailPage } from './forge-detail';

@Component({ selector: 'app-test-forge-detail-host', imports: [RouterOutlet], template: '<router-outlet />' })
class TestForgeDetailHost {}

const refuse = (status: number, body: object): never => stubError(status, body) as never;

describe('ForgeDetailPage', () => {
  let stub: RequestClientStub;
  afterEach(() => {
    stub?.restore();
    document.body.innerHTML = '';
  });

  async function render(opts: { read: () => ReturnType<typeof forgeDetail>; patch?: () => unknown }) {
    stub = stubHubClient({
      'GET /api/me': () => ({ ...OPERATOR_ME_RESPONSE, permissions: ['config:edit'] }),
      'GET /api/forges/{forge}': () => opts.read(),
      'PATCH /api/forges/{forge}': (opts.patch ?? (() => opts.read())) as never,
      'POST /api/forges/{forge}/retire': () => forgeDetail({ retired: true, revision: 9 }),
      'POST /api/forges/{forge}/enable': () => forgeDetail({ revision: 9 }),
      'GET /api/config/changes': (() => ({ changes: [], next_cursor: null })) as never,
    } as never);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    await TestBed.configureTestingModule({
      imports: [TestForgeDetailHost],
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(queryClient),
        provideRouter([{ path: 'admin/forges/:key', component: ForgeDetailPage }]),
        { provide: HUB_PROJECT_FILTER, useValue: signal('acme').asReadonly() },
      ],
    }).compileComponents();
    TestBed.inject(ViewportService).setOverride('desktop');
    const fixture = TestBed.createComponent(TestForgeDetailHost);
    document.body.appendChild(fixture.nativeElement);
    await TestBed.inject(Router).navigateByUrl('/admin/forges/frg_g');
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
    const refetch = async () => {
      await queryClient.invalidateQueries();
      await settle(fixture, 12);
    };
    return { fixture, q, click, type, refetch };
  }

  it('hides the kind, marks slug, name, and API base, and holds Save with no change', async () => {
    const { click, q } = await render({ read: () => forgeDetail({ revision: 2 }) });
    await click('admin-forge-edit');
    expect(q('admin-forge-edit-field-kind')).toBeNull();
    expect(document.querySelectorAll('[data-testid="admin-forge-edit-dialog"] fleet-kit-field .req').length).toBe(3);
    expect(q<HTMLButtonElement>('kit-form-save')!.disabled).toBe(true);
  });

  it('sends the sparse body against the baseline revision after a refetch bumped it', async () => {
    let revision = 2;
    const { click, type, refetch } = await render({ read: () => forgeDetail({ revision }) });
    await click('admin-forge-edit');
    await type('admin-forge-edit-field-name', 'Enterprise');
    revision = 5;
    await refetch();
    await click('kit-form-save');
    const calls = stub.forRoute('/api/forges/frg_g', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBe('2');
    expect(calls[0].body).toEqual({ name: 'Enterprise' });
  });

  it('keeps the draft on a revision conflict on the form line, sends once, and the next edit sends the settled revision', async () => {
    let revision = 2;
    let conflict = true;
    const { click, type, q } = await render({
      read: () => forgeDetail({ revision }),
      patch: () =>
        conflict
          ? refuse(409, { type: 'urn:blizzard:problem:forge-revision-conflict', detail: { code: 'revision_conflict', current: 3, message: 'the forge changed since it was read' } })
          : forgeDetail({ revision: 4 }),
    });
    await click('admin-forge-edit');
    await type('admin-forge-edit-field-webBase', 'https://code.example.test');
    revision = 3;
    await click('kit-form-save');
    expect(stub.forRoute('/api/forges/frg_g', 'PATCH')).toHaveLength(1);
    expect(q('kit-form-line')?.textContent).toContain('the forge changed since it was read');
    expect(q<HTMLInputElement>('admin-forge-edit-field-webBase')!.value).toBe('https://code.example.test');

    conflict = false;
    await click('kit-form-cancel');
    expect(q('admin-forge-edit-dialog')).toBeNull();
    await click('admin-forge-edit');
    await type('admin-forge-edit-field-webBase', 'https://code.example.test');
    await click('kit-form-save');
    const calls = stub.forRoute('/api/forges/frg_g', 'PATCH');
    expect(calls).toHaveLength(2);
    expect(calls[1].headers['if-match']).toBe('3');
    expect(q('admin-forge-edit-dialog')).toBeNull();
  });

  it('retires against the current revision', async () => {
    const { click } = await render({ read: () => forgeDetail({ revision: 2 }) });
    await click('admin-forge-retire');
    const [call] = stub.forRoute('/api/forges/frg_g/retire', 'POST');
    expect(call.headers['if-match']).toBe('2');
  });
});

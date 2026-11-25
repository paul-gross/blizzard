import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import {
  hostSettingsChangeView,
  hostSettingsChangesPage,
  hostWorkspaceView,
  type RequestClientStub,
  settle,
  stubError,
  stubRunnerClient,
} from 'fleet/testing';
import { HostRecordsPanel } from './host-records';

const WEB = hostWorkspaceView({ workspace_id: 'web', revision: 3, settings: { root: '/srv/web' } });
const OLD = hostWorkspaceView({ workspace_id: 'old', revision: 5, state: 'retired', settings: {} });

describe('HostRecordsPanel', () => {
  let stub: RequestClientStub;
  afterEach(() => stub?.restore());

  async function render(options: { superuser?: boolean; patch?: () => unknown } = {}) {
    stub = stubRunnerClient({
      'GET /api/host/workspaces': () => ({ items: [WEB, OLD] }),
      'GET /api/host/settings/changes': () =>
        hostSettingsChangesPage({ changes: [hostSettingsChangeView({ record_kind: 'workspace', record_key: 'web', op: 'create' })] }),
      'POST /api/host/workspaces': () => WEB,
      'PATCH /api/host/workspaces/{workspace_id}': (options.patch ?? (() => WEB)) as never,
      'POST /api/host/workspaces/{workspace_id}/retire': () => ({ ...WEB, state: 'retired' as const }),
      'POST /api/host/workspaces/{workspace_id}/enable': () => OLD,
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })),
      ],
    });
    const fixture = TestBed.createComponent(HostRecordsPanel);
    fixture.componentRef.setInput('kind', 'workspace');
    fixture.componentRef.setInput('superuser', options.superuser ?? true);
    await settle(fixture, 12);
    const el = fixture.nativeElement as HTMLElement;
    const q = (testid: string) =>
      document.querySelector<HTMLElement>(`[data-testid="${testid}"]`) ?? el.querySelector<HTMLElement>(`[data-testid="${testid}"]`);
    const click = async (testid: string) => {
      q(testid)!.click();
      await settle(fixture, 12);
    };
    const type = async (testid: string, value: string) => {
      const input = q(testid) as HTMLInputElement;
      input.value = value;
      input.dispatchEvent(new Event('input', { bubbles: true }));
      await settle(fixture, 12);
    };
    return { fixture, el, q, click, type };
  }

  it('lists every workspace, the retired beside the active, each with its state', async () => {
    const { q } = await render();

    expect(stub.forRoute('/api/host/workspaces', 'GET')[0].search).toBe('?retired=true');
    expect(q('records-workspace-web-state')?.textContent).toContain('active');
    expect(q('records-workspace-old-state')?.textContent).toContain('retired');
  });

  it('opens a record`s detail with its settings and its own history', async () => {
    const { q, click } = await render();

    await click('records-workspace-web-open');

    expect(q('records-workspace-web-setting-root')?.textContent).toContain('/srv/web');
    const history = stub.forRoute('/api/host/settings/changes', 'GET');
    expect(history.at(-1)?.search).toContain('record_kind=workspace');
    expect(history.at(-1)?.search).toContain('record_key=web');
    expect(q('settings-history-workspace-web-change')?.textContent).toContain('create');
  });

  it('shows a session that is not the superuser no verb control', async () => {
    const { q, click } = await render({ superuser: false });

    await click('records-workspace-web-open');
    await click('records-workspace-old-open');

    expect(q('records-workspace-create')).toBeNull();
    expect(q('records-workspace-old-detail')).not.toBeNull();
    for (const verb of ['edit', 'retire', 'enable']) expect(q(`records-workspace-old-${verb}`)).toBeNull();
  });

  it('offers retire of an active record and enable of a retired one', async () => {
    const { q, click } = await render();

    await click('records-workspace-web-open');
    expect(q('records-workspace-web-retire')).not.toBeNull();
    expect(q('records-workspace-web-enable')).toBeNull();
    await click('records-workspace-old-open');
    expect(q('records-workspace-old-enable')).not.toBeNull();
    expect(q('records-workspace-old-retire')).toBeNull();
  });

  it('retires only once confirmed', async () => {
    const { click } = await render();

    await click('records-workspace-web-open');
    await click('records-workspace-web-retire');
    await click('records-workspace-web-retiring-cancel');
    expect(stub.forRoute('/api/host/workspaces/web/retire', 'POST')).toHaveLength(0);

    await click('records-workspace-web-retire');
    await click('records-workspace-web-retiring-confirm');
    expect(stub.forRoute('/api/host/workspaces/web/retire', 'POST')).toHaveLength(1);
  });

  it('enables a retired record', async () => {
    const { click } = await render();

    await click('records-workspace-old-open');
    await click('records-workspace-old-enable');

    expect(stub.forRoute('/api/host/workspaces/old/enable', 'POST')).toHaveLength(1);
  });

  it('creates a workspace from its form', async () => {
    const { q, click, type } = await render();

    await click('records-workspace-create');
    await type('record-form-id', 'api');
    await type('record-form-max_environments', '2');
    await click('record-form-save');

    expect(stub.forRoute('/api/host/workspaces', 'POST')[0].body).toEqual({ id: 'api', max_environments: 2 });
    expect(q('record-form')).toBeNull();
  });

  it('refuses a create missing its id inline, sending nothing', async () => {
    const { q, click } = await render();

    await click('records-workspace-create');
    await click('record-form-save');

    expect(stub.forRoute('/api/host/workspaces', 'POST')).toHaveLength(0);
    expect(q('record-form-id')!.closest('fleet-kit-field')!.textContent).toContain('Required.');
  });

  it('edits against the revision it opened at, keeping the draft on a revision conflict', async () => {
    const { q, click, type } = await render({
      patch: () =>
        stubError(409, {
          type: 'urn:blizzard:problem:revision-conflict',
          title: 'revision conflict',
          status: 409,
          detail: 'the workspace moved',
          code: 'revision_conflict',
          key: null,
          current: 4,
          errors: null,
          record_kind: 'workspace',
          record_key: 'web',
        }),
    });

    await click('records-workspace-web-open');
    await click('records-workspace-web-edit');
    expect((q('record-form-root') as HTMLInputElement).value).toBe('/srv/web');
    await type('record-form-root', '/srv/new');
    await click('record-form-save');

    const calls = stub.forRoute('/api/host/workspaces/web', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBe('3');
    expect(calls[0].body).toEqual({ root: '/srv/new' });
    expect(q('record-form-line')?.textContent).toContain('the workspace moved');
    expect((q('record-form-root') as HTMLInputElement).value).toBe('/srv/new');
  });
});

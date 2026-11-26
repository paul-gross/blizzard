import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import type { HubSettingsChange } from 'fleet';
import { OPERATOR_ME_RESPONSE, settle, stubError, stubHubClient, type RequestClientStub } from 'fleet/testing';
import { HubSettingsPage } from './hub-settings-page';

describe('HubSettingsPage', () => {
  let stub: RequestClientStub;
  afterEach(() => { stub?.restore(); sessionStorage.clear(); document.body.innerHTML = ''; });

  it('does not read or render settings for a tenant superuser', async () => {
    const permissions = OPERATOR_ME_RESPONSE.permissions.filter((p) => p !== 'settings:admin');
    const me = { ...OPERATOR_ME_RESPONSE, role: 'superuser' as const, hub_admin: false, permissions,
      tenants: OPERATOR_ME_RESPONSE.tenants.map((tenant) => ({ ...tenant, role: 'superuser' as const, permissions })) };
    stub = stubHubClient({ 'GET /api/me': () => me });
    await TestBed.configureTestingModule({ imports: [HubSettingsPage], providers: [provideZonelessChangeDetection(), provideRouter([]),
      provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } }))] }).compileComponents();
    const fixture = TestBed.createComponent(HubSettingsPage);
    await settle(fixture);
    expect((fixture.nativeElement as HTMLElement).textContent).toContain('Hub settings are not available');
    expect(stub.forRoute('/api/admin/settings', 'GET')).toHaveLength(0);
    expect(stub.forRoute('/api/admin/settings/changes', 'GET')).toHaveLength(0);
  });

  async function mountAuthorized(
    patch: NonNullable<Parameters<typeof stubHubClient>[0]['PATCH /api/admin/settings']>,
    changes: HubSettingsChange[] = [],
  ) {
    const permissions = [...OPERATOR_ME_RESPONSE.permissions, 'settings:admin' as const];
    const me = { ...OPERATOR_ME_RESPONSE, permissions, tenants: OPERATOR_ME_RESPONSE.tenants.map((t) => ({ ...t, permissions })) };
    let revision = 7;
    const rows = () => ['alpha', 'beta', 'gamma'].map((key) => ({ key, value: 60, lifetime: 'pass' as const,
      source: key === 'gamma' ? ('default' as const) : ('entry' as const), revision: 7, written_at: null, written_by: null }));
    stub = stubHubClient({ 'GET /api/me': () => me, 'GET /api/admin/settings': () => ({ revision, settings: rows() }),
      'GET /api/admin/settings/changes': () => ({ changes, next_before: null }), 'PATCH /api/admin/settings': patch });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } });
    await TestBed.configureTestingModule({ imports: [HubSettingsPage], providers: [provideZonelessChangeDetection(), provideRouter([]),
      provideTanStackQuery(client)] }).compileComponents();
    const fixture = TestBed.createComponent(HubSettingsPage);
    document.body.appendChild(fixture.nativeElement);
    await settle(fixture);
    const refetchAt = async (next: number) => {
      revision = next;
      await client.invalidateQueries();
      await settle(fixture);
    };
    return { fixture, el: fixture.nativeElement as HTMLElement, refetchAt };
  }

  const written = () => stub.forRoute('/api/admin/settings', 'PATCH');
  const byId = <E extends HTMLElement>(id: string) => document.querySelector<E>(`[data-testid="${id}"]`)!;
  const input = () =>
    document.querySelector<HTMLInputElement>('[data-testid="hub-setting-edit-field-value"] input, input[data-testid="hub-setting-edit-field-value"]')!;
  const chip = () => byId('hub-setting-edit-field-useDefault').querySelector<HTMLButtonElement>('fleet-kit-chip button')!;
  const save = () => byId<HTMLButtonElement>('kit-form-save');
  const title = () => byId('hub-setting-edit-dialog-title')?.textContent?.trim();
  const type = (text: string) => {
    input().value = text;
    input().dispatchEvent(new Event('input'));
  };

  function button(el: HTMLElement, text: string): HTMLButtonElement {
    return [...el.querySelectorAll('button')].find((b) => b.textContent?.trim() === text)!;
  }

  it('unsets with the captured revision through the default choice, and closes', async () => {
    const { fixture, el } = await mountAuthorized(() => ({ revision: 8, settings: [] }));
    button(el, 'Edit alpha').click(); await settle(fixture);
    chip().click(); await settle(fixture);
    save().click(); await settle(fixture);
    expect(written()[0].body).toEqual({ unset: ['alpha'] });
    expect(written()[0].headers['if-match']).toBe('7');
    expect(title()).toBeUndefined();
  });

  it('rejects invalid JSON without a write and retains the editor on a conflict', async () => {
    const { fixture, el } = await mountAuthorized(() => stubError(409, { detail: 'conflict' }));
    button(el, 'Edit alpha').click(); await settle(fixture);
    type('no'); await settle(fixture);
    save().click(); await settle(fixture);
    expect(el.textContent).toContain('Enter a valid JSON value.');
    expect(written()).toHaveLength(0);
    type('75'); await settle(fixture);
    save().click(); await settle(fixture);
    expect(byId('kit-form-line').textContent).toContain('conflict');
    expect(title()).toBe('Edit alpha');
    expect(input().value).toBe('75');
  });

  it('prevents switching or cancelling while a write is pending', async () => {
    let release!: (value: { revision: number; settings: [] }) => void;
    const response = new Promise<{ revision: number; settings: [] }>((resolve) => { release = resolve; });
    const { fixture, el } = await mountAuthorized(() => response);
    button(el, 'Edit alpha').click(); await settle(fixture);
    type('75'); await settle(fixture);
    save().click();
    await vi.waitFor(() => { fixture.detectChanges(); expect(button(el, 'Edit beta').disabled).toBe(true); });
    expect(byId<HTMLButtonElement>('kit-form-cancel').disabled).toBe(true);
    button(el, 'Edit beta').click();
    fixture.detectChanges();
    expect(title()).toBe('Edit alpha');
    release({ revision: 8, settings: [] }); await settle(fixture);
  });

  it('opens a key at its default with Default chosen, and sets a stated value', async () => {
    const { fixture, el } = await mountAuthorized(() => ({ revision: 8, settings: [] }));
    button(el, 'Edit gamma').click(); await settle(fixture);
    expect(chip().getAttribute('aria-pressed')).toBe('true');
    expect(input().disabled).toBe(true);
    expect(save().disabled).toBe(true);
    chip().click(); await settle(fixture);
    type('75'); await settle(fixture);
    save().click(); await settle(fixture);
    expect(written()[0].body).toEqual({ set: { gamma: 75 } });
  });

  it('keeps the captured revision across a refetch', async () => {
    const { fixture, el, refetchAt } = await mountAuthorized(() => ({ revision: 9, settings: [] }));
    button(el, 'Edit alpha').click(); await settle(fixture);
    type('75'); await settle(fixture);
    await refetchAt(8);
    expect(input().value).toBe('75');
    save().click(); await settle(fixture);
    expect(written()[0].headers['if-match']).toBe('7');
  });

  it('renders each history change as a change-entry panel', async () => {
    const change = (id: number): HubSettingsChange => ({ id, revision: id, op: 'edit', door: 'api', actor: 'pgross', recorded_at: '2026-01-02T00:00:00Z',
      apply_id: null, diff: [{ field: 'alpha', old: 60, new: 75, old_label: null, new_label: null }] });
    const { el } = await mountAuthorized(() => ({ revision: 8, settings: [] }), [change(2), change(1)]);
    const panels = el.querySelectorAll('app-change-entry-panel');
    expect(panels).toHaveLength(2);
    expect(panels[0].querySelector('[data-testid="admin-change-who"]')?.textContent).toContain('pgross via api');
    expect(panels[0].querySelector('[data-testid="admin-change-row-2"] app-config-field-diff')).not.toBeNull();
    expect(el.querySelector('article h3:not(.tag)')).toBeNull();
  });
});

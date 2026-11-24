import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { type RequestClientStub, runnerSettingRowView, runnerSettingsView, settle, stubError, stubRunnerClient } from 'fleet/testing';
import type { SettingsRecordRef } from './settings.model';
import { SettingsRecordPanel } from './settings-record';

const RUNNER: SettingsRecordRef = { id: 'rn_1', title: 'r-claude', target: { kind: 'runner', runnerId: 'rn_1' }, retired: false };
const BUDGET = 'throttle.budget.usd';
const VIEW = runnerSettingsView({
  revision: 7,
  in_force: { revision: 7, since: '2026-11-20T21:14:00Z', failed: null },
  settings: [runnerSettingRowView({ key: BUDGET, value: 10, source: 'entry' }), runnerSettingRowView({ key: 'name', value: 'r-claude', lifetime: 'immutable' })],
});

const CONFLICT = {
  type: 'urn:blizzard:problem:revision-conflict',
  title: 'revision conflict',
  status: 409,
  detail: 'the record moved since it was read',
  code: 'revision_conflict',
  key: null,
  current: 8,
  errors: null,
  record_kind: null,
  record_key: null,
};

describe('SettingsRecordPanel', () => {
  let stub: RequestClientStub;
  let read: typeof VIEW;
  afterEach(() => stub?.restore());
  beforeEach(() => (read = VIEW));

  async function render(options: { superuser?: boolean; ref?: SettingsRecordRef; patch?: () => unknown } = {}) {
    stub = stubRunnerClient({
      'GET /runners/{runner_id}/api/settings': () => read,
      'PATCH /runners/{runner_id}/api/settings': (options.patch ?? (() => read)) as never,
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })),
      ],
    });
    const fixture = TestBed.createComponent(SettingsRecordPanel);
    fixture.componentRef.setInput('ref', options.ref ?? RUNNER);
    fixture.componentRef.setInput('superuser', options.superuser ?? true);
    await settle(fixture, 12);
    const el = fixture.nativeElement as HTMLElement;
    const q = (testid: string) => document.querySelector<HTMLElement>(`[data-testid="${testid}"]`) ?? el.querySelector<HTMLElement>(`[data-testid="${testid}"]`);
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

  it('shows each key with its lifetime and the in_force line, an Edit only on a live key', async () => {
    const { q } = await render();

    expect(q(`settings-rn_1-value-${BUDGET}`)?.textContent).toContain('10');
    expect(q(`settings-rn_1-lifetime-name`)?.textContent).toContain('immutable');
    expect(q('settings-rn_1-in-force')?.textContent).toContain('running revision 7');
    expect(q(`settings-rn_1-edit-${BUDGET}`)).not.toBeNull();
    expect(q('settings-rn_1-edit-name')).toBeNull();
  });

  it('offers no edit to a session that is not the superuser, nor on a retired runner', async () => {
    const guest = await render({ superuser: false });
    expect(guest.q(`settings-rn_1-edit-${BUDGET}`)).toBeNull();
    guest.fixture.destroy();
    stub.restore();
    TestBed.resetTestingModule();

    const retired = await render({ ref: { ...RUNNER, retired: true } });
    expect(retired.q(`settings-rn_1-edit-${BUDGET}`)).toBeNull();
  });

  it('writes the key against the revision the edit started from, after a refresh moved the read', async () => {
    const { fixture, q, click, type } = await render();

    await click(`settings-rn_1-edit-${BUDGET}`);
    expect((q('setting-edit-value') as HTMLInputElement).value).toBe('10');
    await type('setting-edit-value', '25');
    read = { ...VIEW, revision: 8 };
    await TestBed.inject(QueryClient).invalidateQueries({ queryKey: ['rn_1', 'settings'] });
    await settle(fixture, 12);
    expect((q('setting-edit-value') as HTMLInputElement).value).toBe('25');

    await click('setting-edit-save');

    const calls = stub.forRoute('/runners/rn_1/api/settings', 'PATCH');
    expect(calls).toHaveLength(1);
    expect(calls[0].headers['if-match']).toBe('7');
    expect(calls[0].body).toEqual({ set: { [BUDGET]: 25 } });
    expect(q('setting-edit')).toBeNull();
  });

  it('resets the key to its default', async () => {
    const { click } = await render();

    await click(`settings-rn_1-edit-${BUDGET}`);
    await click('setting-edit-reset');

    expect(stub.forRoute('/runners/rn_1/api/settings', 'PATCH')[0].body).toEqual({ unset: [BUDGET] });
  });

  it('keeps the dialog and the typed draft on a revision conflict', async () => {
    const { q, click, type } = await render({ patch: () => stubError(409, CONFLICT) });

    await click(`settings-rn_1-edit-${BUDGET}`);
    await type('setting-edit-value', '25');
    await click('setting-edit-save');

    expect(stub.forRoute('/runners/rn_1/api/settings', 'PATCH')).toHaveLength(1);
    expect(q('setting-edit-line')?.textContent).toContain('the record moved since it was read');
    expect((q('setting-edit-value') as HTMLInputElement).value).toBe('25');
  });

  it('shows a refusal of the key beside its value', async () => {
    const { q, click, type } = await render({
      patch: () => stubError(422, { ...CONFLICT, status: 422, code: 'settings_invalid', errors: [{ key: BUDGET, message: 'must be positive' }] }),
    });

    await click(`settings-rn_1-edit-${BUDGET}`);
    await type('setting-edit-value', '-1');
    await click('setting-edit-save');

    expect(q('setting-edit-value')!.closest('fleet-kit-field')!.textContent).toContain('must be positive');
  });

  it('refuses text that does not read as a value inline, sending nothing', async () => {
    const { q, click, type } = await render();

    await click(`settings-rn_1-edit-${BUDGET}`);
    await type('setting-edit-value', '{x');
    await click('setting-edit-save');

    expect(stub.forRoute('/runners/rn_1/api/settings', 'PATCH')).toHaveLength(0);
    expect(q('setting-edit-value')!.closest('fleet-kit-field')!.textContent).toContain('Enter a valid JSON value.');
  });

  it('shows why the keys could not be resolved in their place', async () => {
    read = runnerSettingsView({ settings: [], settings_error: 'bad entry' });
    const { q } = await render();

    expect(q('settings-rn_1-error')?.textContent).toContain('bad entry');
    expect(q('settings-rn_1-empty')).toBeNull();
  });
});

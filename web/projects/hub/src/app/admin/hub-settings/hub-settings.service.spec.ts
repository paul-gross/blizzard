import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import type { HubSettingRow } from 'fleet';
import { type RequestClientStub, stubError, stubHubClient } from 'fleet/testing';

import { injectHubSettingsService } from './hub-settings.service';
import { toHubSettingBaseline } from './hub-settings.translate';

const row = (value: unknown): HubSettingRow => ({
  key: 'alpha', value, lifetime: 'pass', source: 'entry', revision: 7, written_at: null, written_by: null,
});

describe('injectHubSettingsService', () => {
  let stub: RequestClientStub;

  function configure(patch: NonNullable<Parameters<typeof stubHubClient>[0]['PATCH /api/admin/settings']>) {
    stub = stubHubClient({ 'PATCH /api/admin/settings': patch });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false }, mutations: { retry: false } } })),
      ],
    });
    return TestBed.runInInjectionContext(() => injectHubSettingsService());
  }
  const written = () => stub.forRoute('/api/admin/settings', 'PATCH');
  const ok = () => ({ revision: 8, settings: [] });

  afterEach(() => stub.restore());

  it('sets the parsed value against the baseline revision', async () => {
    const service = configure(ok);
    expect(await service.edit(toHubSettingBaseline(7, row(60)), { value: '75', useDefault: false })).toEqual({ ok: true });
    expect(written()[0].body).toEqual({ set: { alpha: 75 } });
    expect(written()[0].headers['if-match']).toBe('7');
  });

  it('sends a string key its text as typed', async () => {
    const service = configure(ok);
    await service.edit(toHubSettingBaseline(7, row('a')), { value: '75', useDefault: false });
    expect(written()[0].body).toEqual({ set: { alpha: '75' } });
  });

  it('unsets the key when the default is chosen', async () => {
    const service = configure(ok);
    await service.edit(toHubSettingBaseline(7, row(60)), { value: 'ignored', useDefault: true });
    expect(written()[0].body).toEqual({ unset: ['alpha'] });
    expect(written()[0].headers['if-match']).toBe('7');
  });

  it('refuses unparseable text on value without a write', async () => {
    const service = configure(ok);
    expect(await service.edit(toHubSettingBaseline(7, row(60)), { value: 'no', useDefault: false })).toEqual({
      ok: false, fields: { value: 'Enter a valid JSON value.' }, form: null,
    });
    expect(written()).toHaveLength(0);
  });

  it('answers a settings-invalid refusal for the key on value', async () => {
    const service = configure(() =>
      stubError(422, { type: 'urn:blizzard:problem:settings-invalid', detail: 'refused', errors: { alpha: 'too small' } }),
    );
    expect(await service.edit(toHubSettingBaseline(7, row(60)), { value: '1', useDefault: false })).toEqual({
      ok: false, fields: { value: 'too small' }, form: null,
    });
  });

  it('answers a refused unset on the form line', async () => {
    const service = configure(() =>
      stubError(422, { type: 'urn:blizzard:problem:settings-invalid', detail: 'refused', errors: { alpha: 'too small' } }),
    );
    expect(await service.edit(toHubSettingBaseline(7, row(60)), { value: '60', useDefault: true })).toEqual({
      ok: false, fields: {}, form: 'refused',
    });
  });

  it('answers a revision conflict on the form line, sends once, and leaves the baseline and draft as they were', async () => {
    const service = configure(() => stubError(409, { type: 'urn:blizzard:problem:revision-conflict', detail: 'conflict' }));
    const baseline = toHubSettingBaseline(7, row(60));
    const before = structuredClone(baseline);
    const draft = { value: '75', useDefault: false };
    expect(await service.edit(baseline, draft)).toEqual({ ok: false, fields: {}, form: 'conflict' });
    expect(written()).toHaveLength(1);
    expect(baseline).toEqual(before);
    expect(draft).toEqual({ value: '75', useDefault: false });
  });
});

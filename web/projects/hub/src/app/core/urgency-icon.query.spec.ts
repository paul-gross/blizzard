import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubClient } from 'fleet';
import { vi } from 'vitest';
import { TenantContext } from './auth/tenant-context';
import {
  injectUrgencyIconDataUrl,
  injectUrgencyIconUrl,
  injectUrgencyIconUrls,
  readUrgencyIcon,
  urgencyIconDataUrl,
  type UrgencyIconRef,
} from './urgency-icon.query';

const BYTES = 'image bytes';
const icon = signal<UrgencyIconRef | null>({ key: 'u', digest: 'd1' });
const icons = signal<readonly UrgencyIconRef[]>([{ key: 'u', digest: 'd1' }]);
const tenant = signal<string | null>('ten_a');

@Component({ selector: 'app-icon-host', template: '' })
class IconHost {
  readonly single = injectUrgencyIconUrl(() => icon());
  readonly multi = injectUrgencyIconUrls(() => icons());
  readonly form = injectUrgencyIconDataUrl(() => icon());
}

describe('urgency icon reader', () => {
  let requests: string[];
  let body: () => Blob;
  const { fetch: fetchConfig, baseUrl } = hubClient.getConfig();

  beforeEach(() => {
    requests = [];
    body = () => new Blob([BYTES], { type: 'image/png' });
    icon.set({ key: 'u', digest: 'd1' });
    icons.set([{ key: 'u', digest: 'd1' }]);
    tenant.set('ten_a');
    hubClient.setConfig({
      baseUrl: 'http://hub.test',
      fetch: (async (input: Request) => {
        requests.push(new URL(input.url).pathname);
        const blob = body();
        const response = new Response(BYTES, { headers: { 'Content-Type': blob.type || 'application/octet-stream' } });
        response.blob = async () => blob;
        return response;
      }) as typeof fetch,
    });
    TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        { provide: TenantContext, useValue: { current: tenant } },
      ],
    });
  });
  afterEach(() => {
    hubClient.setConfig({ fetch: fetchConfig, baseUrl });
    vi.restoreAllMocks();
  });

  async function mount() {
    const fixture = TestBed.createComponent(IconHost);
    await fixture.whenStable();
    await vi.waitFor(() => {
      const host = fixture.componentInstance;
      expect([host.form(), host.single(), host.multi()['u'] ?? null]).not.toContain(null);
    });
    await fixture.whenStable();
    return fixture;
  }

  it('shares one cache entry across the single, multi and form observers', async () => {
    const create = vi.spyOn(URL, 'createObjectURL').mockImplementation(() => `blob:${create.mock.calls.length}`);
    const fixture = await mount();
    const host = fixture.componentInstance;
    expect(requests).toEqual(['/api/urgencies/u/icon']);
    expect(TestBed.inject(QueryClient).getQueryCache().getAll()).toHaveLength(1);
    expect(host.single()).toMatch(/^blob:/);
    expect(host.multi()['u']).toMatch(/^blob:/);
    expect(host.single()).not.toBe(host.multi()['u']);
  });

  it('derives the editor data URL byte-for-byte from the shared Blob', async () => {
    const fixture = await mount();
    expect(fixture.componentInstance.form()).toBe(`data:image/png;base64,${btoa(BYTES)}`);
    await expect(urgencyIconDataUrl(new Blob([new Uint8Array([0, 255, 128])], { type: 'image/gif' }))).resolves.toBe('data:image/gif;base64,AP+A');
  });

  it('gives another digest or tenant its own entry', async () => {
    const fixture = await mount();
    icon.set({ key: 'u', digest: 'd2' });
    icons.set([{ key: 'u', digest: 'd2' }]);
    await fixture.whenStable();
    tenant.set('ten_b');
    await fixture.whenStable();
    await vi.waitFor(() => expect(requests).toHaveLength(3));
    const keys = TestBed.inject(QueryClient).getQueryCache().getAll().map((query) => JSON.stringify(query.queryKey));
    expect(new Set(keys).size).toBe(keys.length);
    expect(keys.some((key) => key.includes('ten_a') && key.includes('d2'))).toBe(true);
    expect(keys.some((key) => key.includes('ten_b'))).toBe(true);
  });

  it('reads nothing for a null icon or an absent tenant', async () => {
    icon.set(null);
    icons.set([]);
    const fixture = TestBed.createComponent(IconHost);
    await fixture.whenStable();
    expect(fixture.componentInstance.single()).toBeNull();
    expect(fixture.componentInstance.form()).toBeNull();
    icon.set({ key: 'u', digest: 'd1' });
    icons.set([{ key: 'u', digest: 'd1' }]);
    tenant.set(null);
    await fixture.whenStable();
    expect(requests).toEqual([]);
    expect(fixture.componentInstance.multi()).toEqual({});
  });

  it.each([
    ['an empty', () => new Blob([], { type: 'image/png' })],
    ['a non-image', () => new Blob([BYTES], { type: 'text/plain' })],
  ])('refuses %s Blob', async (_, make) => {
    body = make;
    await expect(readUrgencyIcon('u')).rejects.toThrow('Invalid urgency icon');
  });

  it('revokes an object URL when the icon changes and on destroy', async () => {
    let n = 0;
    vi.spyOn(URL, 'createObjectURL').mockImplementation(() => `blob:${++n}`);
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    const fixture = await mount();
    const first = fixture.componentInstance.single()!;
    const firstMulti = fixture.componentInstance.multi()['u']!;
    icon.set({ key: 'u', digest: 'd2' });
    icons.set([{ key: 'u', digest: 'd2' }]);
    await fixture.whenStable();
    await vi.waitFor(() => expect([fixture.componentInstance.single(), fixture.componentInstance.multi()['u']]).toEqual([expect.stringMatching(/^blob:/), expect.stringMatching(/^blob:/)]));
    expect(fixture.componentInstance.single()).not.toBe(first);
    expect(revoke).toHaveBeenCalledWith(first);
    expect(revoke).toHaveBeenCalledWith(firstMulti);
    const second = fixture.componentInstance.single()!;
    const secondMulti = fixture.componentInstance.multi()['u']!;
    fixture.destroy();
    expect(revoke).toHaveBeenCalledWith(second);
    expect(revoke).toHaveBeenCalledWith(secondMulti);
  });
});

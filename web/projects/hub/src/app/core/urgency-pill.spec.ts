import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';
import { hubUrgencyIconKey, type UrgencyView } from 'fleet';
import { vi } from 'vitest';
import { TenantContext } from './auth/tenant-context';
import { UrgencyPill } from './urgency-pill';

const urgency = { urgency_id: 'u', name: 'Urgent', color: 'red', emoji: null, icon: { digest: 'one', media_type: 'image/png' } } as UrgencyView;

describe('UrgencyPill', () => {
  it('revokes icon URLs on tenant change and destruction', async () => {
    const tenant = signal<string | null>('a');
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    client.setQueryData(hubUrgencyIconKey('a', 'u', 'one'), new Blob(['one'], { type: 'image/png' }));
    client.setQueryData(hubUrgencyIconKey('b', 'u', 'one'), new Blob(['two'], { type: 'image/png' }));
    const create = vi.spyOn(URL, 'createObjectURL').mockReturnValueOnce('blob:one').mockReturnValueOnce('blob:two');
    const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
    TestBed.configureTestingModule({ imports: [UrgencyPill], providers: [provideZonelessChangeDetection(), provideTanStackQuery(client), { provide: TenantContext, useValue: { current: tenant } }] });
    const fixture = TestBed.createComponent(UrgencyPill);
    fixture.componentRef.setInput('urgency', urgency);
    await fixture.whenStable();
    expect(fixture.nativeElement.querySelector('img')?.getAttribute('src')).toBe('blob:one');
    tenant.set('b');
    await fixture.whenStable();
    expect(revoke).toHaveBeenCalledWith('blob:one');
    expect(fixture.nativeElement.querySelector('img')?.getAttribute('src')).toBe('blob:two');
    fixture.destroy();
    expect(revoke).toHaveBeenCalledWith('blob:two');
    create.mockRestore();
    revoke.mockRestore();
    client.clear();
  });
});

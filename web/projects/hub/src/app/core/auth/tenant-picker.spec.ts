import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { membership, session } from './session.builders';
import { TenantPicker } from './tenant-picker';

const TENANTS = [
  membership({ tenant_id: 'ten_a', name: 'Acme', permissions: ['fleet:view', 'user:manage'] }),
  membership({ tenant_id: 'ten_b', name: 'Acme', permissions: ['fleet:view'] }),
];

const ME = session({
  username: 'twohats',
  display_name: 'Two Hats',
  permissions: [],
  tenants: [...TENANTS],
  tenant_resolved: false,
});

async function render(): Promise<{ el: HTMLElement; picker: TenantPicker }> {
  await TestBed.configureTestingModule({
    imports: [TenantPicker],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(TenantPicker);
  fixture.componentRef.setInput('tenants', TENANTS);
  fixture.componentRef.setInput('me', ME);
  await fixture.whenStable();
  return {
    el: fixture.nativeElement as HTMLElement,
    picker: fixture.componentInstance,
  };
}

describe('TenantPicker', () => {
  it('lists every membership by name with its id beside it, so tenants sharing a name stay apart', async () => {
    const { el } = await render();

    const options = [
      ...el.querySelectorAll('[data-testid="tenant-picker-option"]'),
    ].map((o) => [
      o.querySelector('.name')?.textContent?.trim(),
      o.querySelector('.id')?.textContent?.trim(),
    ]);
    expect(options).toEqual([
      ['Acme', 'ten_a'],
      ['Acme', 'ten_b'],
    ]);
    expect(
      el.querySelector('[data-testid="tenant-picker-username"]')?.textContent,
    ).toContain('twohats');
  });

  it('emits the chosen tenant id', async () => {
    const { el, picker } = await render();
    const chosen = vi.fn();
    picker.choose.subscribe(chosen);

    el.querySelectorAll<HTMLElement>(
      '[data-testid="tenant-picker-option"]',
    )[1]?.click();

    expect(chosen).toHaveBeenCalledWith('ten_b');
  });

  it('emits logout when the control is clicked', async () => {
    const { el, picker } = await render();
    const logout = vi.fn();
    picker.logout.subscribe(logout);

    el.querySelector<HTMLElement>(
      '[data-testid="tenant-picker-logout"]',
    )?.click();

    expect(logout).toHaveBeenCalledTimes(1);
  });
});

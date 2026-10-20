import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';
import type { KitAsyncStateValue } from 'fleet';

import type { HostEnvGroup } from './host-environments.model';
import { HostEnvironmentsView } from './host-environments';

const GROUPS: readonly HostEnvGroup[] = [
  {
    workspaceId: 'ws',
    capacity: 2,
    inUse: 1,
    envs: [
      {
        environmentId: 'e1',
        held: true,
        chunkRef: 'N3YJ9',
        chunkId: 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9',
        project: 'console',
        holder: 'r1',
        elsewhere: true,
        projectName: 'Console',
        color: 'amber',
      },
      { environmentId: 'e2', held: false, chunkRef: null, chunkId: null, project: null, holder: null, elsewhere: false, projectName: null, color: null },
    ],
  },
];

describe('HostEnvironmentsView', () => {
  async function render(groups: readonly HostEnvGroup[], state: KitAsyncStateValue): Promise<HTMLElement> {
    await TestBed.configureTestingModule({
      imports: [HostEnvironmentsView],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(HostEnvironmentsView);
    fixture.componentRef.setInput('groups', groups);
    fixture.componentRef.setInput('inUseLabel', '1 / 2 in use');
    fixture.componentRef.setInput('state', state);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('lists each workspace with its use, a held environment naming its chunk, project and runner', async () => {
    const el = await render(GROUPS, 'ready');

    expect(el.querySelector('[data-testid="host-workspace"] .ws-use')?.textContent?.trim()).toBe('1 / 2 in use');
    const held = el.querySelector<HTMLElement>('[data-testid="host-env-held"]')!;
    expect(held.classList.contains('elsewhere')).toBe(true);
    expect(held.querySelector('.chunk')?.textContent?.trim()).toBe('N3YJ9');
    expect(held.querySelector('fleet-kit-swatch')).not.toBeNull();
    expect(held.querySelector('.project')?.textContent?.trim()).toBe('Console');
    expect(held.querySelector('.holder')?.textContent?.trim()).toBe('r1');
    expect(el.querySelector('[data-testid="host-env-free"] .free-lbl')?.textContent?.trim()).toBe('FREE');
  });

  it('says no workspaces are declared only once the read lands empty, never while loading', async () => {
    const loading = await render([], 'loading');
    expect(loading.querySelector('[data-testid="host-environments-empty"]')).toBeNull();
    expect(loading.textContent).toContain('LOADING');

    TestBed.resetTestingModule();
    const empty = await render([], 'empty');
    expect(empty.querySelector('[data-testid="host-environments-empty"]')).not.toBeNull();
  });
});

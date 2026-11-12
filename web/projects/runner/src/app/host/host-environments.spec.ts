import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { NavigationEnd, provideRouter, Router } from '@angular/router';
import type { KitAsyncStateValue } from 'fleet';
import { filter, firstValueFrom } from 'rxjs';

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
        chunkRef: 'C-N3YJ9',
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
  async function render(groups: readonly HostEnvGroup[], state: KitAsyncStateValue, url = '/host'): Promise<HTMLElement> {
    await TestBed.configureTestingModule({
      imports: [HostEnvironmentsView],
      providers: [provideZonelessChangeDetection(), provideRouter([
        { path: 'host', children: [] },
        { path: 'board', children: [] },
      ])],
    }).compileComponents();
    await TestBed.inject(Router).navigateByUrl(url);
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
    expect(held.querySelector('.chunk')?.textContent?.trim()).toBe('C-N3YJ9');
    expect(held.querySelector('fleet-kit-swatch')).not.toBeNull();
    expect(held.querySelector('.project')?.textContent?.trim()).toBe('Console');
    expect(held.querySelector('.holder')?.textContent?.trim()).toBe('r1');
    expect(el.querySelector('[data-testid="host-env-free"] .free-lbl')?.textContent?.trim()).toBe('FREE');
  });

  it('links the held chunk to the local board, replacing the selection and retaining runner and project filters', async () => {
    const el = await render(GROUPS, 'ready', '/host?runner=r1&project=console&chunk=old');
    const link = el.querySelector<HTMLAnchorElement>('a.chunk')!;
    const router = TestBed.inject(Router);
    const target = router.parseUrl(link.getAttribute('href')!);
    expect(target.queryParams).toEqual({ runner: 'r1', project: 'console', chunk: GROUPS[0].envs[0].chunkId });
    expect(link.pathname).toBe('/board');
    const navigated = firstValueFrom(router.events.pipe(filter((event) => event instanceof NavigationEnd)));
    link.click();
    await navigated;
    expect(router.parseUrl(router.url).queryParams).toEqual(target.queryParams);
    expect(router.url.split('?')[0]).toBe('/board');
  });

  it('omits the generated env-ch duplicate but retains custom environment names and the full id tooltip', async () => {
    const held = GROUPS[0].envs[0];
    const generated = `env-${held.chunkId}`;
    const el = await render([{ ...GROUPS[0], envs: [{ ...held, environmentId: generated }, held] }], 'ready');
    const rows = Array.from(el.querySelectorAll('[data-testid="host-env-held"]'));
    expect(rows[0].querySelector('.env-id')?.textContent?.trim()).toBe('env');
    expect(rows[0].querySelector('.env-id')?.getAttribute('title')).toBe(generated);
    expect(rows[0].textContent).not.toContain(generated);
    expect(rows[1].querySelector('.env-id')?.textContent?.trim()).toBe('e1');
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

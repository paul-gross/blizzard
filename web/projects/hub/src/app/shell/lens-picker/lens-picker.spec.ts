import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';
import { QueryClient, provideTanStackQuery } from '@tanstack/angular-query-experimental';

import { OPERATOR_ME_RESPONSE, projectView, type RequestClientStub, settle, stubHubClient } from 'fleet/testing';
import { TAB_TENANT_KEY } from '../../core/auth/tenant-context';
import { provideProjectLens } from '../../core/project-lens';
import { LensPicker } from './lens-picker';

@Component({ selector: 'app-test-lens-view', template: '' })
class TestView {}

const project = (slug: string, name: string, color: string) =>
  projectView({ project_id: `proj_${slug}`, slug, name, color, created_at: '2026-10-01T00:00:00Z' });

describe('LensPicker', () => {
  let stub: RequestClientStub;

  beforeEach(async () => {
    sessionStorage.setItem(TAB_TENANT_KEY, 'ten_acme');
    stub = stubHubClient({
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/projects': () => ({ projects: [project('zeta', 'Zeta', 'pink'), project('glacier', 'Glacier', 'teal')] }),
    });
    await TestBed.configureTestingModule({
      providers: [
        provideZonelessChangeDetection(),
        provideTanStackQuery(new QueryClient({ defaultOptions: { queries: { retry: false } } })),
        provideProjectLens(),
        provideRouter([
          { path: 'lens', component: LensPicker },
          { path: 'p/:projectSlug', children: [{ path: 'lens', component: LensPicker }, { path: '**', component: TestView }] },
          { path: '**', component: TestView },
        ]),
      ],
    }).compileComponents();
  });
  afterEach(() => {
    stub.restore();
    sessionStorage.clear();
  });

  async function open(url: string) {
    const harness = await RouterTestingHarness.create();
    await harness.navigateByUrl(url);
    await settle(harness.fixture);
    const el = harness.fixture.nativeElement as HTMLElement;
    return { harness, el, choice: (slug: string) => el.querySelector<HTMLButtonElement>(`[data-testid="lens-picker-${slug}"]`) };
  }

  it('offers All projects then each project by name, the lens\'s own choice pressed', async () => {
    const { el } = await open('/p/glacier/lens?from=%2Ffleet');

    const choices = [...el.querySelectorAll('.choice')];
    expect(choices.map((c) => c.getAttribute('data-testid'))).toEqual(['lens-picker-all', 'lens-picker-glacier', 'lens-picker-zeta']);
    expect(choices.map((c) => c.querySelector('.name')?.textContent?.trim())).toEqual(['All projects', 'Glacier', 'Zeta']);
    expect(choices.map((c) => c.getAttribute('aria-pressed'))).toEqual(['false', 'true', 'false']);
  });

  it('backs out to the view it was opened from, under the lens it is still on', async () => {
    const { el } = await open('/p/glacier/lens?from=%2Ffleet');

    expect(el.querySelector('[data-testid="lens-picker-back"]')?.getAttribute('href')).toBe('/p/glacier/fleet');
  });

  it('backs out to the board when opened with no view to return to', async () => {
    const { el } = await open('/lens');

    expect(el.querySelector('[data-testid="lens-picker-back"]')?.getAttribute('href')).toBe('/board');
  });

  it('returns to that view under the chosen lens', async () => {
    const { harness, choice } = await open('/lens?from=%2Ffleet');

    choice('zeta')!.click();
    await settle(harness.fixture);
    expect(TestBed.inject(Router).url).toBe('/p/zeta/fleet');
  });

  it('returns to that view on All when All is chosen', async () => {
    const { harness, choice } = await open('/p/glacier/lens?from=%2Fboard%3Flayout%3Dlist');

    choice('all')!.click();
    await settle(harness.fixture);
    expect(TestBed.inject(Router).url).toBe('/board?layout=list');
  });
});

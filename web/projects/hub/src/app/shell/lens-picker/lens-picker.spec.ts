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

  it('backs out to the exact originating location under the lens still on', async () => {
    const from = encodeURIComponent('/board/chunk/chk_1?layout=list#x');
    const { el } = await open(`/p/glacier/lens?from=${from}`);

    expect(el.querySelector('[data-testid="lens-picker-back"]')?.getAttribute('href')).toBe('/p/glacier/board/chunk/chk_1?layout=list#x');
  });

  for (const [mount, prefix] of [['unlensed', ''], ['lensed', '/p/glacier']] as const) {
    describe(`on the ${mount} mount`, () => {
      const at = (from: string | null) => `${prefix}/lens${from === null ? '' : `?from=${encodeURIComponent(from)}`}`;

      it('opens the chosen project on the originating view, dropping its record, query and fragment', async () => {
        const { harness, choice } = await open(at('/board/chunk/chk_1?layout=list#x'));
        choice('zeta')!.click();
        await settle(harness.fixture);
        expect(TestBed.inject(Router).url).toBe('/p/zeta/board');
      });

      it('keeps a gardening tab across a choice', async () => {
        const { harness, choice } = await open(at('/gardening/findings/f1'));
        choice('zeta')!.click();
        await settle(harness.fixture);
        expect(TestBed.inject(Router).url).toBe('/p/zeta/gardening/findings');
      });

      it('keeps an admin tab across a choice of All', async () => {
        const { harness, choice } = await open(at('/admin/members/m1'));
        choice('all')!.click();
        await settle(harness.fixture);
        expect(TestBed.inject(Router).url).toBe('/admin/members');
      });

      it('leaves the picker for the view-only URL when the project already on is chosen', async () => {
        const { harness, choice } = await open(at('/board/chunk/chk_1'));
        choice('glacier')!.click();
        await settle(harness.fixture);
        expect(TestBed.inject(Router).url).toBe('/p/glacier/board');
      });

      for (const from of [null, '', 'fleet', '//evil.example/x', '/lens', '/p/zeta/lens?from=lens']) {
        it(`falls back to the board for a source of ${JSON.stringify(from)}`, async () => {
          const { harness, el, choice } = await open(at(from));
          const lensed = prefix !== '';
          expect(el.querySelector('[data-testid="lens-picker-back"]')?.getAttribute('href')).toBe(lensed ? '/p/glacier/board' : '/board');
          choice('zeta')!.click();
          await settle(harness.fixture);
          expect(TestBed.inject(Router).url).toBe('/p/zeta/board');
        });
      }
    });
  }

  it('returns to the view on All when All is chosen', async () => {
    const { harness, choice } = await open('/p/glacier/lens?from=%2Fboard%3Flayout%3Dlist');

    choice('all')!.click();
    await settle(harness.fixture);
    expect(TestBed.inject(Router).url).toBe('/board');
  });

  it('shows All and Back with a loading line while the projects are pending', async () => {
    stub.restore();
    stub = stubHubClient({
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/projects': () => new Promise(() => undefined),
    });
    const { el } = await open('/lens');

    expect(el.querySelector('[data-testid="lens-picker-loading"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="lens-picker-all"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="lens-picker-back"]')).not.toBeNull();
  });

  it('shows an error line on a failed read, and a no-projects line on an empty tenant', async () => {
    stub.restore();
    stub = stubHubClient({
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/projects': () => {
        throw new Error('down');
      },
    });
    const failed = await open('/lens');
    expect(failed.el.querySelector('[data-testid="lens-picker-error"]')).not.toBeNull();
    expect(failed.el.querySelector('[data-testid="lens-picker-all"]')).not.toBeNull();
  });

  it('shows a no-projects line on an empty tenant', async () => {
    stub.restore();
    stub = stubHubClient({
      'GET /api/me': () => OPERATOR_ME_RESPONSE,
      'GET /api/projects': () => ({ projects: [] }),
    });
    const { el } = await open('/lens');
    expect(el.querySelector('[data-testid="lens-picker-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="lens-picker-all"]')).not.toBeNull();
  });
});

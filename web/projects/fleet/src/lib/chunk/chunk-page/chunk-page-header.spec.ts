import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { ChunkDetail, RouteView, WorkRefView } from '../../api/hub';
import { chunkDetail, routeView } from 'fleet/testing';
import { ChunkPageHeader } from './chunk-page-header';

const CHUNK_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';

@Component({
  selector: 'fleet-chunk-page-header-test-host',
  imports: [ChunkPageHeader],
  template: `<fleet-chunk-page-header
    [chunkId]="chunkId"
    [status]="status"
    [tone]="tone"
    [pointers]="pointers"
    [projectName]="projectName"
    [projectColor]="projectColor"
    [route]="route"
    [detail]="detail"
    [canControl]="canControl"
  />`,
})
class TestHost {
  chunkId = CHUNK_ID;
  status = 'running';
  tone: 'running' | 'needs' = 'running';
  pointers: readonly WorkRefView[] = [];
  projectName: string | null = null;
  projectColor: string | null = null;
  route: RouteView | null = null;
  detail: ChunkDetail | null = null;
  canControl = false;
}

describe('ChunkPageHeader', () => {
  async function render(patch: Partial<TestHost> = {}): Promise<HTMLElement> {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestHost);
    Object.assign(fixture.componentInstance, patch);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('renders the full chunk id, not compactRef’s short form', async () => {
    const el = await render();

    const ref = el.querySelector('[data-testid="mobile-chunk-ref"]');
    expect(ref?.textContent?.trim()).toBe(CHUNK_ID);
    // Pins that the compactRef short form is absent, not merely that the full id is present.
    expect(ref?.textContent).not.toContain('C-3YJ9');
  });

  it('renders the status as plain toned text, not a badge', async () => {
    const el = await render();

    const status = el.querySelector<HTMLElement>('[data-testid="mobile-chunk-status"]');
    expect(status?.textContent?.trim()).toBe('running');
    expect(status?.querySelector('.badge')).toBeNull();
    expect(status?.style.color).not.toBe('');
  });

  it('renders an actual <header> element, ancestor of both the ref and the status', async () => {
    const el = await render();

    const header = el.querySelector('header.cp-hdr');
    expect(header).not.toBeNull();
    expect(header?.querySelector('[data-testid="mobile-chunk-ref"]')).not.toBeNull();
    expect(header?.querySelector('[data-testid="mobile-chunk-status"]')).not.toBeNull();
  });

  it('leads the line with the work items it serves', async () => {
    const el = await render({
      pointers: [
        { source: 'hub', ref: '5', label: 'hub:5', web_url: 'https://example.test/5' },
        { source: 'hub', ref: '6', label: 'hub:6', web_url: null },
      ],
    });

    const pointers = [...el.querySelectorAll('[data-testid="mobile-chunk-pointer"]')].map((n) => n.textContent?.trim());
    expect(pointers).toEqual(['hub:5', 'hub:6']);
    // Two pointers, the status: two gaps, so two dots.
    expect(el.querySelectorAll('.cp-sub .sep')).toHaveLength(2);
  });

  it('leads the line with the project and its swatch, and names the runner holding the route', async () => {
    const el = await render({ projectName: 'blizzard', projectColor: '#4af', route: routeView() });

    const project = el.querySelector('[data-testid="mobile-chunk-project"]');
    expect(project?.textContent?.trim()).toBe('blizzard');
    expect(project?.querySelector('fleet-kit-swatch')).not.toBeNull();
    const runner = el.querySelector('[data-testid="mobile-chunk-runner"]');
    expect(runner?.textContent?.trim()).toContain('r-claude');
    expect(runner?.getAttribute('title')).toContain('rn_01fixture000000000000000000');
    // Project, runner, status: two gaps.
    expect(el.querySelectorAll('.cp-sub .sep')).toHaveLength(2);
  });

  it('omits the project and runner facts it is not given', async () => {
    const el = await render();

    expect(el.querySelector('[data-testid="mobile-chunk-project"]')).toBeNull();
    expect(el.querySelector('[data-testid="mobile-chunk-runner"]')).toBeNull();
    expect(el.querySelectorAll('.cp-sub .sep')).toHaveLength(0);
  });

  it('draws the ⋮ actions menu only with control and a detail to act on', async () => {
    expect((await render({ detail: chunkDetail() })).querySelector('[data-testid="chunk-actions-menu"]')).toBeNull();
    TestBed.resetTestingModule();
    expect((await render({ canControl: true })).querySelector('[data-testid="chunk-actions-menu"]')).toBeNull();
    TestBed.resetTestingModule();
    const el = await render({ canControl: true, detail: chunkDetail() });
    const menu = el.querySelector('[data-testid="chunk-actions-menu"]');
    expect(menu).not.toBeNull();
    expect(menu?.querySelector('svg')).not.toBeNull();
  });
});

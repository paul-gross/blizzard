import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ChunkArtifactDelta } from './chunk-artifact-delta';
import type { FindingDelta } from './parse-finding-delta';

const RAW = JSON.stringify({ scope: 'runner-daemon', findings: [] });

const FULL_DELTA: FindingDelta = {
  scope: 'runner-daemon',
  revisions: {
    blizzard: '05eb39ec51ccf5fc3773dca6af414d059fcba1d5',
    'blizzard-context': 'cefb4fc3ddde874ab6c3785b75fcde49f2fa6fcf',
  },
  measurement: '225 Python files swept; 11 findings opened.',
  findings: [
    {
      op: 'add',
      class: 'wide-seam',
      locus: 'src/a.py::IHarnessAdapter',
      summary: 'IHarnessAdapter declares 15 methods spanning five unrelated jobs.',
      introduced: '38faf3daf',
      ref: 'F1',
    },
    { op: 'observed', id: 'fin_01M1HFVFQDA8MA0230NQ4E5QSM' },
    { op: 'gone', id: 'fin_01M1HFVFQDQSE4YD3KZDAX6SDR', note: 'no longer reproduces' },
  ],
};

async function mount(delta: FindingDelta, raw = RAW, testid?: string) {
  await TestBed.configureTestingModule({
    imports: [ChunkArtifactDelta],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkArtifactDelta);
  fixture.componentRef.setInput('delta', delta);
  fixture.componentRef.setInput('raw', raw);
  if (testid !== undefined) fixture.componentRef.setInput('testid', testid);
  await fixture.whenStable();
  return { fixture, el: fixture.nativeElement as HTMLElement };
}

describe('ChunkArtifactDelta', () => {
  it('renders the scope fact and, once revisions are present, a revisions row shortening each sha with the full value in a title', async () => {
    const { el } = await mount(FULL_DELTA);

    expect(el.querySelector('[data-testid="artifact-delta-scope"]')?.textContent).toContain('runner-daemon');
    const revisions = el.querySelector('[data-testid="artifact-delta-revisions"]');
    expect(revisions?.textContent).toContain('blizzard');
    expect(revisions?.textContent).toContain('05eb39ec51'); // shortened
    expect(revisions?.textContent).not.toContain('05eb39ec51ccf5fc3773dca6af414d059fcba1d5'); // full sha not in text
    expect(revisions?.querySelector('[title="05eb39ec51ccf5fc3773dca6af414d059fcba1d5"]')).toBeTruthy();
  });

  it('omits the revisions row entirely when the delta names none, rather than rendering an empty row', async () => {
    const { el } = await mount({ ...FULL_DELTA, revisions: {} });

    expect(el.querySelector('[data-testid="artifact-delta-revisions"]')).toBeNull();
  });

  it('renders the measurement as prose', async () => {
    const { el } = await mount(FULL_DELTA);
    expect(el.querySelector('[data-testid="artifact-delta-measurement"]')?.textContent).toContain('225 Python files swept');
  });

  it('omits the measurement block when the delta carries none', async () => {
    const { el } = await mount({ ...FULL_DELTA, measurement: null });
    expect(el.querySelector('[data-testid="artifact-delta-measurement"]')).toBeNull();
  });

  it('groups findings by op, each group headed with its own count', async () => {
    const { el } = await mount(FULL_DELTA);

    const added = el.querySelector('[data-testid="artifact-delta-added"]');
    expect(added?.textContent).toContain('wide-seam');
    expect(added?.textContent).toContain('src/a.py::IHarnessAdapter');
    expect(added?.textContent).toContain('IHarnessAdapter declares 15 methods');
    expect(added?.querySelector('h5')?.textContent).toContain('1');

    const observed = el.querySelector('[data-testid="artifact-delta-observed"]');
    expect(observed?.textContent).toContain('F-5QSM'); // compactRef of fin_...5QSM

    const gone = el.querySelector('[data-testid="artifact-delta-gone"]');
    expect(gone?.textContent).toContain('no longer reproduces');
  });

  it('hides a group with no entries rather than rendering it empty', async () => {
    const { el } = await mount({ ...FULL_DELTA, findings: FULL_DELTA.findings.filter((f) => f.op === 'add') });

    expect(el.querySelector('[data-testid="artifact-delta-added"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="artifact-delta-observed"]')).toBeNull();
    expect(el.querySelector('[data-testid="artifact-delta-gone"]')).toBeNull();
  });

  it('starts on the structured view, with the raw JSON reachable behind the toggle', async () => {
    const raw = JSON.stringify(FULL_DELTA);
    const { el, fixture } = await mount(FULL_DELTA, raw);

    expect(el.querySelector('[data-testid="artifact-delta-raw"]')).toBeNull();
    expect(el.querySelector('[data-testid="artifact-delta-added"]')).toBeTruthy();

    el.querySelector<HTMLButtonElement>('[data-testid="artifact-delta-raw-toggle"]')?.click();
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="artifact-delta-raw"]')?.textContent).toBe(raw);
    expect(el.querySelector('[data-testid="artifact-delta-added"]')).toBeNull();

    el.querySelector<HTMLButtonElement>('[data-testid="artifact-delta-raw-toggle"]')?.click();
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="artifact-delta-raw"]')).toBeNull();
    expect(el.querySelector('[data-testid="artifact-delta-added"]')).toBeTruthy();
  });

  it('roots every handle under a custom testid, so two mounts never collide', async () => {
    const { el } = await mount(FULL_DELTA, RAW, 'mobile-artifact');

    expect(el.querySelector('[data-testid="mobile-artifact-delta-scope"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="artifact-delta-scope"]')).toBeNull();
  });
});

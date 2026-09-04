import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ChunkArtifactSurvey } from './chunk-artifact-survey';
import type { FindingSurvey } from './parse-finding-survey';

const FULL_SURVEY: FindingSurvey = {
  scope: 'runner-daemon',
  revisions: {
    blizzard: '05eb39ec51ccf5fc3773dca6af414d059fcba1d5',
  },
  measurement: '225 Python files swept; 11 candidates recorded.',
  candidates: [
    {
      ref: 'F1',
      class: 'wide-seam',
      locus: 'src/a.py::IHarnessAdapter',
      summary: 'IHarnessAdapter declares 15 methods spanning five unrelated jobs.',
      introduced: '38faf3daf',
    },
  ],
};

const RAW = JSON.stringify(FULL_SURVEY);

async function mount(survey: FindingSurvey, raw = RAW, testid?: string) {
  TestBed.resetTestingModule();
  await TestBed.configureTestingModule({
    imports: [ChunkArtifactSurvey],
    providers: [provideZonelessChangeDetection()],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkArtifactSurvey);
  fixture.componentRef.setInput('survey', survey);
  fixture.componentRef.setInput('raw', raw);
  if (testid !== undefined) fixture.componentRef.setInput('testid', testid);
  await fixture.whenStable();
  return { fixture, el: fixture.nativeElement as HTMLElement };
}

describe('ChunkArtifactSurvey', () => {
  it('renders the scope fact and a revisions row shortening each sha with the full value in a title', async () => {
    const { el } = await mount(FULL_SURVEY);

    expect(el.querySelector('[data-testid="artifact-survey-scope"]')?.textContent).toContain('runner-daemon');
    const revisions = el.querySelector('[data-testid="artifact-survey-revisions"]');
    expect(revisions?.textContent).toContain('05eb39ec51');
    expect(revisions?.textContent).not.toContain('05eb39ec51ccf5fc3773dca6af414d059fcba1d5');
    expect(revisions?.querySelector('[title="05eb39ec51ccf5fc3773dca6af414d059fcba1d5"]')).toBeTruthy();
  });

  it('omits the revisions row entirely when the survey names none', async () => {
    const { el } = await mount({ ...FULL_SURVEY, revisions: {} });

    expect(el.querySelector('[data-testid="artifact-survey-revisions"]')).toBeNull();
  });

  it('renders the measurement as prose', async () => {
    const { el } = await mount(FULL_SURVEY);

    expect(el.querySelector('[data-testid="artifact-survey-measurement"]')?.textContent).toContain('225 Python files');
  });

  it('omits the measurement block when the survey carries none', async () => {
    const { el } = await mount({ ...FULL_SURVEY, measurement: null });

    expect(el.querySelector('[data-testid="artifact-survey-measurement"]')).toBeNull();
  });

  it('renders each candidate with its class, locus, ref, introduced revision, and summary', async () => {
    const { el } = await mount(FULL_SURVEY);

    const candidates = el.querySelector('[data-testid="artifact-survey-candidates"]');
    expect(candidates?.textContent).toContain('wide-seam');
    expect(candidates?.textContent).toContain('src/a.py::IHarnessAdapter');
    expect(candidates?.textContent).toContain('F1');
    expect(candidates?.textContent).toContain('38faf3daf');
    expect(el.querySelector('[data-testid="artifact-survey-candidate-summary-0"]')?.textContent).toContain(
      'IHarnessAdapter declares 15 methods',
    );
    expect(candidates?.querySelector('h5')?.textContent).toContain('1');
  });

  it('still renders the candidate group on a clean sweep, saying so rather than omitting the list', async () => {
    const { el } = await mount({ ...FULL_SURVEY, candidates: [] });

    expect(el.querySelector('[data-testid="artifact-survey-candidates"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="artifact-survey-clean"]')?.textContent).toContain('clean sweep');
  });

  it('starts on the structured view, with the raw JSON reachable behind the toggle', async () => {
    const { el, fixture } = await mount(FULL_SURVEY);

    expect(el.querySelector('[data-testid="artifact-survey-raw"]')).toBeNull();
    expect(el.querySelector('[data-testid="artifact-survey-candidates"]')).toBeTruthy();

    el.querySelector<HTMLButtonElement>('[data-testid="artifact-survey-raw-toggle"]')?.click();
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="artifact-survey-raw"]')?.textContent).toBe(RAW);
    expect(el.querySelector('[data-testid="artifact-survey-candidates"]')).toBeNull();

    el.querySelector<HTMLButtonElement>('[data-testid="artifact-survey-raw-toggle"]')?.click();
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="artifact-survey-candidates"]')).toBeTruthy();
  });

  it('roots every handle under a custom testid, so two mounts never collide', async () => {
    const { el } = await mount(FULL_SURVEY, RAW, 'mobile-artifact');

    expect(el.querySelector('[data-testid="mobile-artifact-survey-scope"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="artifact-survey-scope"]')).toBeNull();
  });
});

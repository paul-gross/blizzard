import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ArtifactView } from '../api/hub';
import { formatAbsolute } from '../when';
import { ChunkArtifactBody } from './chunk-artifact-body';

const ASSET: ArtifactView = {
  epoch: 1,
  key: 'plan',
  kind: 'asset',
  name: 'plan',
  node_id: 'nd_build',
  node_name: 'build',
  recorded_at: '2026-07-13T00:00:01Z',
  content: 'the plan',
};

describe('ChunkArtifactBody', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkArtifactBody],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it("carries the full local datetime as the recency stamp's tooltip, not the raw ISO instant (issue #175)", async () => {
    const fixture = TestBed.createComponent(ChunkArtifactBody);
    fixture.componentRef.setInput('artifact', ASSET);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const stamp = el.querySelector('.a-when');
    expect(stamp?.getAttribute('title')).toBe(formatAbsolute(ASSET.recorded_at));
    expect(stamp?.getAttribute('title')).not.toBe(ASSET.recorded_at);
  });

  it('renders no when stamp, and so no title, when the artifact carries no recorded_at', async () => {
    const fixture = TestBed.createComponent(ChunkArtifactBody);
    fixture.componentRef.setInput('artifact', { ...ASSET, recorded_at: null });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('.a-when')).toBeNull();
  });

  it('omits an asset’s content in summary mode, keeping the head (issue #160)', async () => {
    const fixture = TestBed.createComponent(ChunkArtifactBody);
    fixture.componentRef.setInput('artifact', ASSET);
    fixture.componentRef.setInput('body', 'summary');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="artifact-content"]')).toBeNull();
    expect(el.querySelector('[data-testid="artifact-key"]')?.textContent).toBe('plan');
  });

  it('renders the full asset content by default', async () => {
    const fixture = TestBed.createComponent(ChunkArtifactBody);
    fixture.componentRef.setInput('artifact', ASSET);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="artifact-content"]')?.textContent).toBe('the plan');
  });

  it('keeps a git_commit’s ref line in summary mode', async () => {
    const commit: ArtifactView = {
      epoch: 1,
      key: 'deliver.commit',
      kind: 'git_commit',
      name: 'commit',
      node_id: 'nd_deliver',
      node_name: 'deliver',
      repo: 'acme/widget',
      branch_name: 'feat/widget',
      commit_hash: 'c1',
    };
    const fixture = TestBed.createComponent(ChunkArtifactBody);
    fixture.componentRef.setInput('artifact', commit);
    fixture.componentRef.setInput('body', 'summary');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="artifact-ref"]')?.textContent).toContain('acme/widget');
  });

  it('renders a FindingDelta-shaped asset structurally through ChunkArtifactDelta instead of the verbatim pre', async () => {
    const delta: ArtifactView = {
      ...ASSET,
      key: 'reconcile.delta.2',
      content: JSON.stringify({
        scope: 'runner-daemon',
        findings: [{ op: 'add', class: 'wide-seam', locus: 'a.py:1', summary: 'seam too wide' }],
      }),
    };
    const fixture = TestBed.createComponent(ChunkArtifactBody);
    fixture.componentRef.setInput('artifact', delta);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="artifact-content"]')).toBeNull();
    expect(el.querySelector('[data-testid="artifact-delta"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="artifact-delta-added"]')?.textContent).toContain('wide-seam');
  });

  it('renders a survey-shaped asset through ChunkArtifactSurvey, not as a delta and not verbatim', async () => {
    const survey: ArtifactView = {
      ...ASSET,
      key: 'survey.survey.1',
      content: JSON.stringify({
        scope: 'runner-daemon',
        revisions: { blizzard: 'abc123' },
        measurement: '225 Python files swept',
        candidates: [{ ref: 'F1', class: 'wide-seam', locus: 'a.py:1', summary: 'seam too wide' }],
      }),
    };
    const fixture = TestBed.createComponent(ChunkArtifactBody);
    fixture.componentRef.setInput('artifact', survey);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="artifact-survey"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="artifact-survey-candidates"]')?.textContent).toContain('wide-seam');
    // The head the two shapes share must not make a survey read as a delta.
    expect(el.querySelector('[data-testid="artifact-delta"]')).toBeNull();
    expect(el.querySelector('[data-testid="artifact-content"]')).toBeNull();
  });

  it('falls back to the verbatim pre for JSON matching neither garden shape', async () => {
    const config: ArtifactView = {
      ...ASSET,
      key: 'settings',
      content: JSON.stringify({ scope: 'runner-daemon', revisions: { blizzard: 'abc123' } }),
    };
    const fixture = TestBed.createComponent(ChunkArtifactBody);
    fixture.componentRef.setInput('artifact', config);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="artifact-delta"]')).toBeNull();
    expect(el.querySelector('[data-testid="artifact-survey"]')).toBeNull();
    expect(el.querySelector('[data-testid="artifact-content"]')?.textContent).toContain('revisions');
  });

  it('falls back to the verbatim pre for a delta-shaped asset rendered in summary mode — no parse attempted', async () => {
    const delta: ArtifactView = {
      ...ASSET,
      content: JSON.stringify({ scope: 's', findings: [] }),
    };
    const fixture = TestBed.createComponent(ChunkArtifactBody);
    fixture.componentRef.setInput('artifact', delta);
    fixture.componentRef.setInput('body', 'summary');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="artifact-delta"]')).toBeNull();
    expect(el.querySelector('[data-testid="artifact-content"]')).toBeNull();
  });

  it('attempts no survey parse either in summary mode', async () => {
    const survey: ArtifactView = {
      ...ASSET,
      content: JSON.stringify({ scope: 's', candidates: [] }),
    };
    const fixture = TestBed.createComponent(ChunkArtifactBody);
    fixture.componentRef.setInput('artifact', survey);
    fixture.componentRef.setInput('body', 'summary');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="artifact-survey"]')).toBeNull();
    expect(el.querySelector('[data-testid="artifact-content"]')).toBeNull();
  });
});

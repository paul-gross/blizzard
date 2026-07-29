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
});

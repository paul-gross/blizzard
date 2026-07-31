import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import type { ArtifactView } from 'fleet';

import { ChunkArtifactsTab } from './chunk-artifacts-tab';

const OLDER: ArtifactView = {
  key: 'build.branch.1',
  kind: 'git_commit',
  name: 'branch',
  node_id: 'nd_build',
  node_name: 'build',
  epoch: 1,
  repo: 'acme/widget',
  branch_name: 'feature/x',
  commit_hash: 'abc1234',
  recorded_at: '2026-07-16T11:10:00.000Z',
};

const NEWER: ArtifactView = {
  key: 'review.findings.2',
  kind: 'asset',
  name: 'findings',
  node_id: 'nd_review',
  node_name: 'review',
  epoch: 2,
  content: 'THE FINDINGS BODY',
  recorded_at: '2026-07-16T11:30:00.000Z',
};

describe('ChunkArtifactsTab', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkArtifactsTab],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('lists every entry in recorded_at order, oldest first', async () => {
    const fixture = TestBed.createComponent(ChunkArtifactsTab);
    fixture.componentRef.setInput('artifacts', [NEWER, OLDER]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const rows = Array.from(el.querySelectorAll<HTMLElement>('[data-testid="artifacts-tab-nav-item"]'));
    expect(rows.map((r) => r.getAttribute('data-artifact-key'))).toEqual([OLDER.key, NEWER.key]);
  });

  it('emits pickArtifact with a clicked row’s key', async () => {
    const fixture = TestBed.createComponent(ChunkArtifactsTab);
    fixture.componentRef.setInput('artifacts', [OLDER, NEWER]);
    let selected: string | undefined;
    fixture.componentInstance.pickArtifact.subscribe((key) => (selected = key));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLButtonElement>(`[data-artifact-key="${OLDER.key}"]`)?.click();
    expect(selected).toBe(OLDER.key);
  });

  it('defaults the viewer to the most recent entry when no key is selected', async () => {
    const fixture = TestBed.createComponent(ChunkArtifactsTab);
    fixture.componentRef.setInput('artifacts', [OLDER, NEWER]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="artifacts-tab-artifact"]')?.textContent).toContain('THE FINDINGS BODY');
    const active = el.querySelector('[data-testid="artifacts-tab-nav-item"].active');
    expect(active?.getAttribute('data-artifact-key')).toBe(NEWER.key);
  });

  it('renders the selected entry’s full content, and a git_commit’s ref rather than content', async () => {
    const fixture = TestBed.createComponent(ChunkArtifactsTab);
    fixture.componentRef.setInput('artifacts', [OLDER, NEWER]);
    fixture.componentRef.setInput('selectedKey', OLDER.key);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const viewer = el.querySelector('[data-testid="artifacts-tab-artifact"]');
    expect(viewer?.textContent).toContain('acme/widget');
    expect(viewer?.textContent).not.toContain('THE FINDINGS BODY');
  });

  /**
   * The viewer's identity handle, asserted here — in the component that owns it —
   * because `demo/demo-director.ts` reads it in **production** to know which
   * artifact is on screen before it starts scrolling.
   *
   * It is the one board handle nothing else pins: `artifacts-tab-artifact-key`
   * is never a literal in the components at all (`ChunkArtifactBody` synthesizes
   * it as `` `${testid()}-key` ``), so neither a grep nor a browser scenario
   * catches a rename — and the director's own spec uses stand-ins, which would
   * only prove it agrees with itself. Break this and the kiosk stops scrolling
   * artifacts, silently. Change it here and the director changes with it.
   */
  it('renders the selected key inside the viewer — the handle demo mode steers by', async () => {
    const fixture = TestBed.createComponent(ChunkArtifactsTab);
    fixture.componentRef.setInput('artifacts', [OLDER, NEWER]);
    fixture.componentRef.setInput('selectedKey', OLDER.key);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const viewer = el.querySelector('[data-testid="artifacts-tab-artifact"]');
    const key = viewer?.querySelector('[data-testid="artifacts-tab-artifact-key"]');
    expect(key?.textContent?.trim()).toBe(OLDER.key);
  });

  it('resolves a key naming nothing in the store to the empty state, not a silent fallback', async () => {
    const fixture = TestBed.createComponent(ChunkArtifactsTab);
    fixture.componentRef.setInput('artifacts', [OLDER, NEWER]);
    fixture.componentRef.setInput('selectedKey', 'gone.missing.9');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="artifacts-tab-empty"]')?.textContent).toContain('NO SUCH ARTIFACT');
    expect(el.querySelector('[data-testid="artifacts-tab-artifact"]')).toBeNull();
  });

  it('renders the empty state for an empty store', async () => {
    const fixture = TestBed.createComponent(ChunkArtifactsTab);
    fixture.componentRef.setInput('artifacts', []);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="artifacts-tab-nav-empty"]')?.textContent).toContain('No artifacts yet');
    expect(el.querySelector('[data-testid="artifacts-tab-empty"]')?.textContent).toContain('No artifacts yet');
  });
});

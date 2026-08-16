import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { ChunkDetail } from '../api/hub';
import { ChunkArtifacts } from './chunk-artifacts';

const REVIEW_FAIL_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01review0000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_build',
  latest_epoch: 2,
  work_refs: [],
  history: [],
  artifacts: [
    {
      key: 'build.widget.1',
      kind: 'git_commit',
      name: 'widget',
      node_id: 'nd_build',
      node_name: 'build',
      epoch: 1,
      repo: 'acme/widget',
      branch_name: 'b',
      commit_hash: 'c1',
    },
    {
      key: 'review.review-findings.2',
      kind: 'asset',
      name: 'review-findings',
      node_id: 'nd_review',
      node_name: 'review',
      epoch: 2,
      content: 'BLOCKING: the widget endpoint returns 500 on empty input; add a guard.',
    },
  ],
};

const NAMED_DETAIL: ChunkDetail = {
  chunk_id: 'ch_01named000000000000000000000',
  graph_id: 'gr_1',
  status: 'running',
  current_node_id: 'nd_review',
  current_node_name: 'review',
  latest_epoch: 1,
  work_refs: [],
  history: [],
  artifacts: [
    {
      key: 'build.widget.1',
      kind: 'git_commit',
      name: 'widget',
      node_id: 'nd_build',
      node_name: 'build',
      epoch: 1,
      repo: 'acme/widget',
      branch_name: 'feature/widget',
      commit_hash: 'c1',
      branch_url: 'https://forge.example/acme/widget/tree/feature/widget',
    },
    {
      key: 'build.orphan.1',
      kind: 'git_commit',
      name: 'orphan',
      node_id: 'nd_build',
      node_name: 'build',
      epoch: 1,
      repo: 'acme/orphan',
      branch_name: 'feature/orphan',
      commit_hash: 'c2',
      branch_url: null,
    },
  ],
};

describe('ChunkArtifacts', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [ChunkArtifacts],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
  });

  it('renders no asset content inline — only a summary head — while keeping the git-commit reference (issue #160)', async () => {
    const fixture = TestBed.createComponent(ChunkArtifacts);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-kind="asset"] [data-testid="artifact-content"]')).toBeNull();
    expect(el.querySelector('[data-kind="asset"] [data-testid="artifact-key"]')?.textContent).toContain(
      'review.review-findings.2',
    );

    const commitRef = el.querySelector('[data-kind="git_commit"] [data-testid="artifact-ref"]');
    expect(commitRef?.textContent).toContain('acme/widget');
    expect(commitRef?.textContent).toContain('c1');
  });

  it('renders each row as a link to the chunk detail page’s Artifacts tab, that artifact pre-selected', async () => {
    const fixture = TestBed.createComponent(ChunkArtifacts);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const links = [...el.querySelectorAll<HTMLAnchorElement>('[data-testid="artifact"] a.artifact-link')];
    expect(links).toHaveLength(2);
    // Sorted oldest-first (recorded_at is absent on both fixtures here, so store order):
    // the git_commit row links with its own key.
    const commitLink = links.find((a) => a.getAttribute('href')?.includes('build.widget.1'));
    expect(commitLink?.getAttribute('href')).toBe(
      `/board/chunk/${REVIEW_FAIL_DETAIL.chunk_id}?tab=artifacts&artifact=build.widget.1`,
    );
    const assetLink = links.find((a) => a.getAttribute('href')?.includes('review.review-findings.2'));
    expect(assetLink?.getAttribute('href')).toBe(
      `/board/chunk/${REVIEW_FAIL_DETAIL.chunk_id}?tab=artifacts&artifact=review.review-findings.2`,
    );
  });

  it('orders rows by recorded_at, oldest first', async () => {
    const older = { ...REVIEW_FAIL_DETAIL.artifacts![0], key: 'older', recorded_at: '2026-07-13T00:00:01Z' };
    const newer = { ...REVIEW_FAIL_DETAIL.artifacts![1], key: 'newer', recorded_at: '2026-07-13T00:00:02Z' };
    const fixture = TestBed.createComponent(ChunkArtifacts);
    fixture.componentRef.setInput('detail', { ...REVIEW_FAIL_DETAIL, artifacts: [newer, older] });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const keys = [...el.querySelectorAll('[data-testid="artifact-key"]')].map((k) => k.textContent?.trim());
    expect(keys).toEqual(['older', 'newer']);
  });

  it('shows the artifact branch name and links it to the forge, degrading when no url (issue #23)', async () => {
    const fixture = TestBed.createComponent(ChunkArtifacts);
    fixture.componentRef.setInput('detail', NAMED_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const [linked, orphan] = [...el.querySelectorAll('[data-kind="git_commit"] [data-testid="artifact-ref"]')];
    const link = linked.querySelector<HTMLAnchorElement>('a[data-testid="artifact-branch"]');
    expect(link?.textContent?.trim()).toBe('feature/widget');
    expect(link?.getAttribute('href')).toBe('https://forge.example/acme/widget/tree/feature/widget');
    expect(orphan.querySelector('a')).toBeNull();
    expect(orphan.querySelector('[data-testid="artifact-branch"]')?.textContent?.trim()).toBe('feature/orphan');
  });

  it('in expandable mode, renders rows as buttons that toggle full content in place rather than linking away', async () => {
    const fixture = TestBed.createComponent(ChunkArtifacts);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    fixture.componentRef.setInput('expandable', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('a.artifact-link')).toHaveLength(0);
    // Only the asset gets a toggle: the git_commit row has nothing an expand
    // would reveal (see the `hasBodyToExpand` case below).
    const buttons = [...el.querySelectorAll<HTMLButtonElement>('button.artifact-link')];
    expect(buttons).toHaveLength(1);

    const assetRow = el.querySelector('[data-kind="asset"]') as HTMLElement;
    expect(assetRow.querySelector('[data-testid="artifact-content"]')).toBeNull();

    assetRow.querySelector<HTMLButtonElement>('button.artifact-link')?.click();
    await fixture.whenStable();
    expect(assetRow.querySelector('[data-testid="artifact-content"]')?.textContent).toContain(
      'BLOCKING: the widget endpoint returns 500 on empty input',
    );

    // Toggling again collapses it back to summary.
    assetRow.querySelector<HTMLButtonElement>('button.artifact-link')?.click();
    await fixture.whenStable();
    expect(assetRow.querySelector('[data-testid="artifact-content"]')).toBeNull();
  });

  it('in expandable mode, leaves a git_commit row un-toggled so its branch link is not nested in a button', async () => {
    // The ordinary case for the runner's page: every build node's own commit is a
    // `git_commit` with a `branch_url`. `ChunkArtifactBody` renders that ref line the
    // same in `summary` and `full`, so a toggle over it would announce an expansion
    // that changes nothing — and would put a real `<a>` inside a `<button>`.
    const fixture = TestBed.createComponent(ChunkArtifacts);
    fixture.componentRef.setInput('detail', NAMED_DETAIL);
    fixture.componentRef.setInput('expandable', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('button.artifact-link')).toHaveLength(0);
    expect(el.querySelectorAll('[data-testid="artifact-plain"]')).toHaveLength(2);
    const branch = el.querySelector<HTMLAnchorElement>('a[data-testid="artifact-branch"]');
    expect(branch?.getAttribute('href')).toBe('https://forge.example/acme/widget/tree/feature/widget');
    expect(branch?.closest('button')).toBeNull();
  });

  it('in expandable mode, leaves a contentless asset un-toggled too', async () => {
    const fixture = TestBed.createComponent(ChunkArtifacts);
    fixture.componentRef.setInput('detail', {
      ...REVIEW_FAIL_DETAIL,
      artifacts: [{ ...REVIEW_FAIL_DETAIL.artifacts![1], content: '' }],
    });
    fixture.componentRef.setInput('expandable', true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('button.artifact-link')).toHaveLength(0);
    expect(el.querySelector('[data-testid="artifact-plain"] [data-testid="artifact-key"]')?.textContent).toContain(
      'review.review-findings.2',
    );
  });

  it('renders its own "Artifacts" heading by default (issue #205)', async () => {
    const fixture = TestBed.createComponent(ChunkArtifacts);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('#chunk-artifacts-heading')?.textContent).toBe('Artifacts');
  });

  it('omits its own heading when a consumer already supplies one, e.g. a wrapping fleet-kit-panel (issue #205)', async () => {
    const fixture = TestBed.createComponent(ChunkArtifacts);
    fixture.componentRef.setInput('detail', REVIEW_FAIL_DETAIL);
    fixture.componentRef.setInput('heading', false);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('#chunk-artifacts-heading')).toBeNull();
    expect(el.textContent).not.toContain('Artifacts');
  });

  it('shows an empty state when the chunk has no artifacts yet', async () => {
    const fixture = TestBed.createComponent(ChunkArtifacts);
    fixture.componentRef.setInput('detail', { ...REVIEW_FAIL_DETAIL, artifacts: [] });
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="artifacts-empty"]')).not.toBeNull();
    expect(el.querySelector('[data-testid="artifacts"]')).toBeNull();
  });
});

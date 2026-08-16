import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import { type RunnerChunkDetailTab, injectChunkDetailSelection } from './chunk-detail-selection';

/** A minimal host mounting {@link injectChunkDetailSelection} directly, so its
 * URL contract is proven without a real `ChunkDetailPage` in the way — the
 * page's own spec (`chunk-detail-page.spec.ts`) proves it wired to the tab
 * strip's clicks. */
@Component({ selector: 'app-test-selection-host', template: '' })
class SelectionHost {
  readonly selection = injectChunkDetailSelection();
}

const ROUTES = [{ path: 'board/chunk/:chunkId', component: SelectionHost }];

describe('injectChunkDetailSelection', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideRouter(ROUTES)],
    });
  });

  async function open(url: string): Promise<SelectionHost> {
    const harness = await RouterTestingHarness.create();
    return (await harness.navigateByUrl(url, SelectionHost)) as SelectionHost;
  }

  it('resolves an absent ?tab to "general"', async () => {
    const host = await open('/board/chunk/ch_1');
    expect(host.selection.tab()).toBe('general');
  });

  it('resolves an unrecognized ?tab value to "general"', async () => {
    const host = await open('/board/chunk/ch_1?tab=not-a-real-tab');
    expect(host.selection.tab()).toBe('general');
  });

  it('resolves a recognized ?tab value verbatim', async () => {
    const host = await open('/board/chunk/ch_1?tab=transcripts');
    expect(host.selection.tab()).toBe('transcripts');
  });

  it('writes select() to ?tab= with no full reload', async () => {
    const harness = await RouterTestingHarness.create();
    const host = (await harness.navigateByUrl('/board/chunk/ch_1', SelectionHost)) as SelectionHost;

    host.selection.select('artifacts' as RunnerChunkDetailTab);
    await harness.fixture.whenStable();

    expect(TestBed.inject(Router).url).toBe('/board/chunk/ch_1?tab=artifacts');
  });

  it('leaves ?attempt= untouched across a tab switch — select() merges rather than replaces', async () => {
    const harness = await RouterTestingHarness.create();
    const host = (await harness.navigateByUrl('/board/chunk/ch_1?attempt=lease_1', SelectionHost)) as SelectionHost;

    host.selection.select('transcripts');
    await harness.fixture.whenStable();

    expect(TestBed.inject(Router).url).toBe('/board/chunk/ch_1?attempt=lease_1&tab=transcripts');
  });
});

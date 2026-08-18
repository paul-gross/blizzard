import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import { injectChunkDetailSelection } from './chunk-detail-selection';

/** A minimal host mounting {@link injectChunkDetailSelection} directly, so its URL
 * contract is proven without a real `ChunkPage` in the way — mirrors the runner app's
 * own `chunk-detail-selection.spec.ts`. */
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

  it('resolves a recognized "node-history" ?tab value verbatim', async () => {
    const host = await open('/board/chunk/ch_1?tab=node-history');
    expect(host.selection.tab()).toBe('node-history');
  });

  it('resolves an absent ?step to null', async () => {
    const host = await open('/board/chunk/ch_1?tab=node-history');
    expect(host.selection.stepKey()).toBeNull();
  });

  it('resolves ?step verbatim, unvalidated', async () => {
    const host = await open('/board/chunk/ch_1?tab=node-history&step=nd_build:1');
    expect(host.selection.stepKey()).toBe('nd_build:1');
  });

  it('writes selectStep() to ?tab=node-history&step=, merging rather than replacing', async () => {
    const harness = await RouterTestingHarness.create();
    const host = (await harness.navigateByUrl('/board/chunk/ch_1?attempt=lease_1', SelectionHost)) as SelectionHost;

    host.selection.selectStep('nd_build:1');
    await harness.fixture.whenStable();

    expect(TestBed.inject(Router).url).toBe('/board/chunk/ch_1?attempt=lease_1&tab=node-history&step=nd_build:1');
  });

  it('leaves ?step= untouched when select() switches to a different tab (D4)', async () => {
    const harness = await RouterTestingHarness.create();
    const host = (
      await harness.navigateByUrl('/board/chunk/ch_1?tab=node-history&step=nd_build:1', SelectionHost)
    ) as SelectionHost;

    host.selection.select('artifacts', null);
    await harness.fixture.whenStable();

    expect(TestBed.inject(Router).url).toBe('/board/chunk/ch_1?tab=artifacts&step=nd_build:1');
    expect(host.selection.stepKey()).toBe('nd_build:1');
  });
});

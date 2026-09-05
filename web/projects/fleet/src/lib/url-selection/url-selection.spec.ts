import { Location } from '@angular/common';
import { provideLocationMocks } from '@angular/common/testing';
import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';
import { RouterTestingHarness } from '@angular/router/testing';

import { settle } from '../testing/settle';
import { injectChunkUrlSelection } from './url-selection';

/** A minimal host mounting {@link injectChunkUrlSelection} directly, so its
 * URL contract is proven without a real consumer in the way — each
 * consumer's own spec proves it wired to its list/card clicks. */
@Component({ selector: 'fleet-test-selection-host', template: '' })
class SelectionHost {
  readonly selection = injectChunkUrlSelection();
}

const ROUTES = [{ path: 'page', component: SelectionHost }];

describe('injectChunkUrlSelection', () => {
  beforeEach(() => {
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideRouter(ROUTES), provideLocationMocks()],
    });
  });

  async function open(url: string): Promise<SelectionHost> {
    const harness = await RouterTestingHarness.create();
    return (await harness.navigateByUrl(url, SelectionHost)) as SelectionHost;
  }

  it('reads the initial chunkId off a deep-linked ?chunk= param on the first render', async () => {
    const host = await open('/page?chunk=ch_1');
    expect(host.selection.chunkId()).toBe('ch_1');
  });

  it('resolves to null with no chunk param', async () => {
    const host = await open('/page');
    expect(host.selection.chunkId()).toBeNull();
  });

  it('select() merges ?chunk= into the URL without clobbering another param', async () => {
    const harness = await RouterTestingHarness.create();
    const host = (await harness.navigateByUrl('/page?other=kept', SelectionHost)) as SelectionHost;

    host.selection.select('ch_2');
    await harness.fixture.whenStable();

    expect(TestBed.inject(Router).url).toBe('/page?other=kept&chunk=ch_2');
    expect(host.selection.chunkId()).toBe('ch_2');
  });

  it('select(null) clears the chunk param', async () => {
    const harness = await RouterTestingHarness.create();
    const host = (await harness.navigateByUrl('/page?chunk=ch_3', SelectionHost)) as SelectionHost;

    host.selection.select(null);
    await harness.fixture.whenStable();

    expect(TestBed.inject(Router).url).toBe('/page');
    expect(host.selection.chunkId()).toBeNull();
  });

  it('back and forward walk the selection history', async () => {
    const harness = await RouterTestingHarness.create();
    TestBed.inject(Router).setUpLocationChangeListener();
    const host = (await harness.navigateByUrl('/page', SelectionHost)) as SelectionHost;

    host.selection.select('ch_1');
    await harness.fixture.whenStable();
    host.selection.select('ch_2');
    await harness.fixture.whenStable();
    expect(TestBed.inject(Router).url).toBe('/page?chunk=ch_2');

    TestBed.inject(Location).back();
    await settle(harness.fixture);
    expect(TestBed.inject(Router).url).toBe('/page?chunk=ch_1');

    TestBed.inject(Location).forward();
    await settle(harness.fixture);
    expect(TestBed.inject(Router).url).toBe('/page?chunk=ch_2');
  });
});

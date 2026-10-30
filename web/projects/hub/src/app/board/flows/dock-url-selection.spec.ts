import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { Router, provideRouter } from '@angular/router';

import { settle } from 'fleet/testing';
import { injectDockUrlSelection } from './dock-url-selection';

@Component({ selector: 'app-test-dock-host', template: '' })
class DockHost {
  readonly dock = injectDockUrlSelection();
}

describe('injectDockUrlSelection', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [DockHost],
      providers: [provideZonelessChangeDetection(), provideRouter([{ path: '**', component: DockHost }])],
    }).compileComponents();
  });

  async function hostAt(url: string) {
    const router = TestBed.inject(Router);
    await router.navigateByUrl(url);
    const fixture = TestBed.createComponent(DockHost);
    await settle(fixture);
    return { fixture, router, dock: fixture.componentInstance.dock };
  }

  it('opens a flow\'s record from `?flow=`, clearing an open chunk and keeping every other param', async () => {
    const { fixture, router, dock } = await hostAt('/board?chunk=ch_a&layout=list');
    expect(dock.selection()).toEqual({ view: 'chunk', chunkId: 'ch_a' });

    dock.selectFlow('flw_1');
    await settle(fixture);

    expect(router.url).toBe('/board?layout=list&flow=flw_1');
    expect(dock.selection()).toEqual({ view: 'flow', flowId: 'flw_1' });
    expect(dock.chunkId()).toBeNull();
  });

  it('opens a chunk, clearing an open flow record', async () => {
    const { fixture, router, dock } = await hostAt('/board?flow=flw_1');
    expect(dock.flowId()).toBe('flw_1');

    dock.selectChunk('ch_a');
    await settle(fixture);

    expect(router.url).toBe('/board?chunk=ch_a');
    expect(dock.flowId()).toBeNull();
  });

  it('shows the chunk, not the flow, for a hand-written URL naming both', async () => {
    const { dock } = await hostAt('/board?chunk=ch_a&flow=flw_1');
    expect(dock.selection()).toEqual({ view: 'chunk', chunkId: 'ch_a' });
    expect(dock.flowId()).toBeNull();
  });
});

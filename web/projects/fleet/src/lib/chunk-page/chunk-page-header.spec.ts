import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { ChunkPageHeader } from './chunk-page-header';

const CHUNK_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';

@Component({
  selector: 'fleet-chunk-page-header-test-host',
  imports: [ChunkPageHeader],
  template: `<fleet-chunk-page-header [chunkId]="chunkId" [status]="status" [tone]="tone" />`,
})
class TestHost {
  chunkId = CHUNK_ID;
  status = 'running';
  tone: 'running' | 'needs' = 'running';
}

describe('ChunkPageHeader', () => {
  async function render(): Promise<HTMLElement> {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    return fixture.nativeElement as HTMLElement;
  }

  it('renders the full chunk id, not compactRef’s short form', async () => {
    const el = await render();

    const ref = el.querySelector('[data-testid="mobile-chunk-ref"]');
    expect(ref?.textContent?.trim()).toBe(CHUNK_ID);
    // The old short form this header used to render, so a regression back to
    // it fails loudly rather than by omission.
    expect(ref?.textContent).not.toContain('C-3YJ9');
  });

  it('renders the status text on a soft-tone badge', async () => {
    const el = await render();

    const status = el.querySelector('[data-testid="mobile-chunk-status"]');
    expect(status?.textContent?.trim()).toBe('running');
    expect(status?.querySelector('.badge')?.classList.contains('soft')).toBe(true);
  });

  it('renders an actual <header> element, ancestor of both the ref and the status', async () => {
    const el = await render();

    const header = el.querySelector('header.cp-hdr');
    expect(header).not.toBeNull();
    expect(header?.querySelector('[data-testid="mobile-chunk-ref"]')).not.toBeNull();
    expect(header?.querySelector('[data-testid="mobile-chunk-status"]')).not.toBeNull();
  });
});

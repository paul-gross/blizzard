import { Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import type { WorkRefView } from '../api/hub';
import { ChunkPageHeader } from './chunk-page-header';

const CHUNK_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';

@Component({
  selector: 'fleet-chunk-page-header-test-host',
  imports: [ChunkPageHeader],
  template: `<fleet-chunk-page-header
    [chunkId]="chunkId"
    [status]="status"
    [tone]="tone"
    [pointers]="pointers"
    [blockedBy]="blockedBy"
    [blocking]="blocking"
  />`,
})
class TestHost {
  chunkId = CHUNK_ID;
  status = 'running';
  tone: 'running' | 'needs' = 'running';
  pointers: readonly WorkRefView[] = [];
  blockedBy: readonly string[] = [];
  blocking: readonly string[] = [];
}

describe('ChunkPageHeader', () => {
  async function render(patch: Partial<TestHost> = {}): Promise<HTMLElement> {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection(), provideRouter([])],
    }).compileComponents();
    const fixture = TestBed.createComponent(TestHost);
    Object.assign(fixture.componentInstance, patch);
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

  it('renders the status as plain toned text, not a badge', async () => {
    const el = await render();

    const status = el.querySelector<HTMLElement>('[data-testid="mobile-chunk-status"]');
    expect(status?.textContent?.trim()).toBe('running');
    expect(status?.querySelector('.badge')).toBeNull();
    expect(status?.style.color).not.toBe('');
  });

  it('renders an actual <header> element, ancestor of both the ref and the status', async () => {
    const el = await render();

    const header = el.querySelector('header.cp-hdr');
    expect(header).not.toBeNull();
    expect(header?.querySelector('[data-testid="mobile-chunk-ref"]')).not.toBeNull();
    expect(header?.querySelector('[data-testid="mobile-chunk-status"]')).not.toBeNull();
  });

  it('leads the line with the work items it serves', async () => {
    const el = await render({
      pointers: [
        { source: 'hub', ref: '5', label: 'hub:5', web_url: 'https://example.test/5' },
        { source: 'hub', ref: '6', label: 'hub:6', web_url: null },
      ],
    });

    const pointers = [...el.querySelectorAll('[data-testid="mobile-chunk-pointer"]')].map((n) => n.textContent?.trim());
    expect(pointers).toEqual(['hub:5', 'hub:6']);
    // Two pointers, the status: two gaps, so two dots.
    expect(el.querySelectorAll('.cp-sub .sep')).toHaveLength(2);
  });

  it('names no edge for a chunk carrying none', async () => {
    const el = await render();

    expect(el.querySelector('[data-testid="mobile-chunk-blocked-by"]')).toBeNull();
    expect(el.querySelector('[data-testid="mobile-chunk-blocking"]')).toBeNull();
  });

  it('links only the ref it names, not the whole phrase', async () => {
    const el = await render({
      blockedBy: ['ch_01prereq00000000000000000'],
      blocking: ['ch_01dependent000000000000aa'],
    });

    const blockedBy = el.querySelector('[data-testid="mobile-chunk-blocked-by"]')!;
    const blocking = el.querySelector('[data-testid="mobile-chunk-blocking"]')!;
    expect(blockedBy.textContent?.replace(/\s+/g, ' ').trim()).toBe('blocked by C-0000');
    expect(blocking.textContent?.replace(/\s+/g, ' ').trim()).toBe('blocking C-00aa');

    // The phrase itself is not the target — only the ref inside it is.
    expect(blockedBy.tagName).toBe('SPAN');
    const link = blockedBy.querySelector<HTMLAnchorElement>('a.edge-ref')!;
    expect(link.getAttribute('href')).toBe('/board/chunk/ch_01prereq00000000000000000');
    expect(link.textContent?.trim()).toBe('C-0000');
    expect(blocking.querySelector('a.edge-ref')?.getAttribute('href')).toBe(
      '/board/chunk/ch_01dependent000000000000aa',
    );
  });
});

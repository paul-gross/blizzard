import { provideZonelessChangeDetection } from '@angular/core';
import { type ComponentFixture, TestBed } from '@angular/core/testing';
import { provideRouter } from '@angular/router';

import { ChunkBlocked } from './chunk-blocked';

const PREREQUISITE_ID = 'ch_01KXKVVF1J3D6H6VYZ3XYN3YJ9';

async function render(asLink = false): Promise<{ fixture: ComponentFixture<ChunkBlocked>; el: HTMLElement }> {
  await TestBed.configureTestingModule({
    imports: [ChunkBlocked],
    providers: [provideZonelessChangeDetection(), provideRouter([])],
  }).compileComponents();
  const fixture = TestBed.createComponent(ChunkBlocked);
  fixture.componentRef.setInput('prerequisiteChunkId', PREREQUISITE_ID);
  if (asLink) fixture.componentRef.setInput('asLink', true);
  await fixture.whenStable();
  return { fixture, el: fixture.nativeElement as HTMLElement };
}

describe('ChunkBlocked', () => {
  it('names the prerequisite by its compact ref, not the full id', async () => {
    const { el } = await render();

    const marking = el.querySelector('[data-testid="chunk-blocked"]');
    expect(marking?.textContent).toContain('C-3YJ9');
    expect(marking?.textContent).not.toContain(PREREQUISITE_ID);
  });

  it('renders a dock-select button, not a link, when asLink is false (the default)', async () => {
    const { el } = await render();

    expect(el.querySelector('button[data-testid="chunk-blocked"]')).not.toBeNull();
    expect(el.querySelector('a[data-testid="chunk-blocked"]')).toBeNull();
  });

  it('emits selectChunk with the prerequisite id when the dock-select button is clicked', async () => {
    const { fixture, el } = await render();
    let emitted: string | undefined;
    fixture.componentInstance.selectChunk.subscribe((chunkId) => (emitted = chunkId));

    el.querySelector<HTMLButtonElement>('[data-testid="chunk-blocked"]')?.click();

    expect(emitted).toBe(PREREQUISITE_ID);
  });

  it('renders a routerLink under linkBase, not a button, when asLink is true', async () => {
    const { el } = await render(true);

    const marking = el.querySelector('a[data-testid="chunk-blocked"]');
    expect(marking).not.toBeNull();
    expect(marking?.getAttribute('href')).toBe(`/board/chunk/${PREREQUISITE_ID}`);
    expect(el.querySelector('button[data-testid="chunk-blocked"]')).toBeNull();
  });
});

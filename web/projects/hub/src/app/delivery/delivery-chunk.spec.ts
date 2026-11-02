import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { DeliveryChunk } from './delivery-chunk';
import type { DeliveryChunkRef } from './delivery.model';

const REF: DeliveryChunkRef = { chunkId: 'ch_1', shortId: 'C-0001', lines: [], titlesPending: false, tint: null, projectName: null };

const lines = (n: number) => Array.from({ length: n }, (_, i) => ({ label: `hub#${i + 1}`, title: `Item ${i + 1}` }));

describe('DeliveryChunk', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({ imports: [DeliveryChunk], providers: [provideZonelessChangeDetection()] }).compileComponents();
  });

  async function render(ref: Partial<DeliveryChunkRef>) {
    const fixture = TestBed.createComponent(DeliveryChunk);
    fixture.componentRef.setInput('ref', { ...REF, ...ref });
    await fixture.whenStable();
    return fixture;
  }

  const texts = (el: HTMLElement, selector: string) => [...el.querySelectorAll(selector)].map((node) => node.textContent?.trim());

  it('names each work item by its ref over its title', async () => {
    const el = (await render({ lines: lines(2) })).nativeElement as HTMLElement;
    expect(texts(el, '.ref')).toEqual(['hub#1', 'hub#2']);
    expect(texts(el, '.title')).toEqual(['Item 1', 'Item 2']);
  });

  it('names a chunk with no work items by its short id alone', async () => {
    const el = (await render({})).nativeElement as HTMLElement;
    expect(texts(el, '.ref')).toEqual(['C-0001']);
  });

  it('holds a skeleton in place of each title while the titles are read', async () => {
    const el = (await render({ lines: [{ label: 'hub#1', title: null }], titlesPending: true })).nativeElement as HTMLElement;
    expect(el.querySelectorAll('.title-skeleton')).toHaveLength(1);
    expect(el.querySelector('.title')).toBeNull();
  });

  it('folds a long group behind a disclosure that opens and closes it', async () => {
    const fixture = await render({ lines: lines(5) });
    const el = fixture.nativeElement as HTMLElement;
    const fold = () => el.querySelector<HTMLButtonElement>('[data-testid="delivery-chunk-fold"]');

    expect(texts(el, '.ref')).toEqual(['hub#1', 'hub#2']);
    expect(fold()?.textContent?.trim()).toBe('+3 more');
    expect(fold()?.getAttribute('aria-expanded')).toBe('false');

    fold()!.click();
    await fixture.whenStable();
    expect(texts(el, '.ref')).toHaveLength(5);
    expect(fold()?.textContent?.trim()).toBe('show less');

    fold()!.click();
    await fixture.whenStable();
    expect(texts(el, '.ref')).toHaveLength(2);
  });

  it('offers no disclosure for a group short enough to show whole', async () => {
    const el = (await render({ lines: lines(3) })).nativeElement as HTMLElement;
    expect(el.querySelector('[data-testid="delivery-chunk-fold"]')).toBeNull();
  });
});

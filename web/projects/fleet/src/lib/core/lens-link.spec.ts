import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { provideRouter, RouterLink } from '@angular/router';
import { vi } from 'vitest';

import { IN_APP_LINK, type InAppLink, LensLink } from './lens-link';

@Component({
  selector: 'fleet-test-lens-link-host',
  imports: [LensLink, RouterLink],
  template: `<a data-testid="board" [routerLink]="'/board' | lensLink">Board</a>
    <a data-testid="chunk" [routerLink]="['/board', 'chunk', id] | lensLink">Chunk</a>`,
})
class TestLensLinkHost {
  readonly id = 'chk_1';
}

describe('LensLink', () => {
  it('hands every link back unchanged when the app provides no lens', async () => {
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection(), provideRouter([])] });
    const fixture = TestBed.createComponent(TestLensLinkHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="board"]')?.getAttribute('href')).toBe('/board');
    expect(el.querySelector('[data-testid="chunk"]')?.getAttribute('href')).toBe('/board/chunk/chk_1');
  });

  it('follows the lens the app provides, as it moves', async () => {
    const prefix = signal<string | null>('blizzard');
    const rewrite = (link: InAppLink): InAppLink => {
      const slug = prefix();
      if (slug === null) return link;
      return typeof link === 'string' ? `/p/${slug}${link}` : ['/p', slug, ...link.map((part, i) => (i === 0 ? String(part).slice(1) : part))];
    };
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), provideRouter([]), { provide: IN_APP_LINK, useValue: rewrite }],
    });
    const fixture = TestBed.createComponent(TestLensLinkHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="board"]')?.getAttribute('href')).toBe('/p/blizzard/board');
    expect(el.querySelector('[data-testid="chunk"]')?.getAttribute('href')).toBe('/p/blizzard/board/chunk/chk_1');

    prefix.set(null);
    fixture.changeDetectorRef.markForCheck();
    await fixture.whenStable();

    expect(el.querySelector('[data-testid="board"]')?.getAttribute('href')).toBe('/board');
  });

  it('hands back the same link while the lens leaves it unchanged', () => {
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
    const pipe = TestBed.runInInjectionContext(() => new LensLink());

    const first = pipe.transform(['/board', 'chunk', 'chk_1']);
    expect(pipe.transform(['/board', 'chunk', 'chk_1'])).toBe(first);
    expect(pipe.transform(['/board', 'chunk', 'chk_2'])).not.toBe(first);
  });

  it('hands a missing link back as it is, without asking the lens', () => {
    const rewrite = vi.fn((link: InAppLink) => link);
    TestBed.configureTestingModule({
      providers: [provideZonelessChangeDetection(), { provide: IN_APP_LINK, useValue: rewrite }],
    });
    const pipe = TestBed.runInInjectionContext(() => new LensLink());

    expect(pipe.transform(null)).toBeNull();
    expect(pipe.transform(undefined)).toBeUndefined();
    expect(rewrite).not.toHaveBeenCalled();
  });
});

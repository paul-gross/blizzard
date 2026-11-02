import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { type ComponentFixture, TestBed } from '@angular/core/testing';
import { vi } from 'vitest';

import { injectScrollSelectionIntoView } from './scroll-selection-into-view';

@Component({
  selector: 'app-test-scroll-host',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `
    <div class="scroller" style="overflow-y: auto" [attr.data-selected]="selected()">
      @for (id of ids(); track id) {
        <div class="item" [attr.data-id]="id">{{ id }}</div>
      }
    </div>
  `,
})
class ScrollHost {
  readonly ids = signal(['a', 'b']);
  readonly selected = signal<string | null>(null);

  constructor() {
    injectScrollSelectionIntoView(this.selected, (id) => `.item[data-id="${id}"]`);
  }
}

describe('injectScrollSelectionIntoView', () => {
  let scrolled: { id: string | null; options: ScrollIntoViewOptions | undefined }[];
  let observed: Element[];
  let disconnects: number;
  let reducedMotion: boolean;

  beforeEach(async () => {
    scrolled = [];
    observed = [];
    disconnects = 0;
    reducedMotion = false;
    vi.spyOn(Element.prototype, 'scrollIntoView').mockImplementation(function (this: Element, options?: boolean | ScrollIntoViewOptions) {
      scrolled.push({ id: this.getAttribute('data-id'), options: options as ScrollIntoViewOptions | undefined });
    });
    vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({ matches: reducedMotion, media: query }) as MediaQueryList);
    // A ResizeObserver reports once on observe, as the browser's does.
    vi.stubGlobal(
      'ResizeObserver',
      class {
        constructor(private readonly cb: ResizeObserverCallback) {}
        observe(target: Element): void {
          observed.push(target);
          this.cb([], this as unknown as ResizeObserver);
        }
        unobserve(): void {
          // Never called by the reveal.
        }
        disconnect(): void {
          disconnects++;
        }
      },
    );
    await TestBed.configureTestingModule({ imports: [ScrollHost], providers: [provideZonelessChangeDetection()] }).compileComponents();
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  /** Settle a change, then the animation frame the reveal scrolls on. */
  async function render(fixture: ComponentFixture<ScrollHost>): Promise<void> {
    await fixture.whenStable();
    await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
  }

  async function mount(selected: string | null = null) {
    const fixture = TestBed.createComponent(ScrollHost);
    fixture.componentInstance.selected.set(selected);
    await render(fixture);
    return fixture;
  }

  it('reveals nothing while nothing is selected', async () => {
    await mount();

    expect(scrolled).toEqual([]);
  });

  it('reveals a selection already set on load, to the nearest edge of its scroll container, gliding', async () => {
    const fixture = await mount('b');

    expect(scrolled).toEqual([{ id: 'b', options: { block: 'nearest', inline: 'nearest', behavior: 'smooth' } }]);
    expect(observed).toEqual([(fixture.nativeElement as HTMLElement).querySelector('.scroller')]);
  });

  it('reveals each new pick once, and not again for the same pick', async () => {
    const fixture = await mount('a');
    fixture.componentInstance.selected.set('b');
    await render(fixture);
    fixture.componentInstance.selected.set('b');
    fixture.componentInstance.ids.set(['a', 'b', 'c']);
    await render(fixture);

    expect(scrolled.map((s) => s.id)).toEqual(['a', 'b']);
  });

  it('waits for a pick that has not rendered yet, then reveals it', async () => {
    const fixture = await mount('c');
    expect(scrolled).toEqual([]);

    fixture.componentInstance.ids.set(['a', 'b', 'c']);
    await render(fixture);
    expect(scrolled.map((s) => s.id)).toEqual(['c']);
  });

  it('jumps rather than glides under reduced motion', async () => {
    reducedMotion = true;
    await mount('a');

    expect(scrolled[0].options?.behavior).toBe('auto');
  });

  it('stops watching the container once a later pick takes over, and on destroy', async () => {
    const fixture = await mount('a');
    fixture.componentInstance.selected.set('b');
    await render(fixture);
    expect(disconnects).toBe(1);

    fixture.destroy();
    expect(disconnects).toBe(2);
  });
});

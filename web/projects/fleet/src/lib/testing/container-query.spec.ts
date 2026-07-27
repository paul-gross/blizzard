import { ChangeDetectionStrategy, Component, provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { hiddenAtContainerWidth } from './container-query';

/** An unrelated component contributing exactly the `@container` shapes the helper
 * does not speak — an unnamed one and a range condition — to the same document. */
@Component({
  selector: 'fleet-zz-noise',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<div class="outer"><span class="inner">x</span></div>`,
  styles: `
    .outer { container: card / inline-size; }
    .inner { display: inline; }
    @container (min-width: 600px) { .inner { display: none; } }
    @container card (width > 40em) { .inner { display: block; } }
  `,
})
class Noise {}

@Component({
  selector: 'fleet-zz-subject',
  changeDetection: ChangeDetectionStrategy.OnPush,
  template: `<header class="bar"><span class="cell">x</span></header>`,
  styles: `
    .bar { container-name: probe-bar; container-type: inline-size; }
    .cell { display: flex; }
    @container probe-bar (max-width: 900px) { .cell { display: none; } }
  `,
})
class Subject {}

describe('resolveContainerStyle', () => {
  /*
   * Every component's styles land in the same jsdom document, so this helper sees
   * `@container` rules it was never asked about — including the two shapes its
   * deliberately narrow grammar does not speak, an unnamed container and a range
   * condition. Asking about one container must not fail over another's rule, in
   * some unrelated file, that the spec's author never touched.
   */
  it('ignores rules for other containers instead of dying on their grammar', async () => {
    await TestBed.configureTestingModule({
      imports: [Noise, Subject],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
    const noise = TestBed.createComponent(Noise);
    await noise.whenStable();
    const subject = TestBed.createComponent(Subject);
    await subject.whenStable();
    const cell = (subject.nativeElement as HTMLElement).querySelector('.cell')!;

    expect(hiddenAtContainerWidth(cell, { containerName: 'probe-bar', width: 1000 })).toBe(false);
    expect(hiddenAtContainerWidth(cell, { containerName: 'probe-bar', width: 800 })).toBe(true);
  });
});

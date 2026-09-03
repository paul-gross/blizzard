import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitProseBlock } from './kit-prose-block';

@Component({
  selector: 'fleet-test-host',
  imports: [KitProseBlock],
  template: `
    <fleet-kit-prose-block [kind]="kind()" [label]="label()" [text]="text()" testid="prose-a" />
  `,
})
class TestHost {
  readonly kind = signal<'output' | 'context'>('output');
  readonly label = signal<string | null>(null);
  readonly text = signal('line one\nline two');
}

describe('KitProseBlock', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the text body but no label when label is null', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('.who')).toBeNull();
    const text = el.querySelector('.tx') as HTMLElement;
    expect(text.textContent).toBe('line one\nline two');
    expect(el.querySelector('[data-testid="prose-a"]')).toBeTruthy();
  });

  it('renders the label once set', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.label.set('Rationale');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('.who')?.textContent).toBe('Rationale');
  });

  it('reads as agent output by default, the transcript\u2019s own dark tick', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // The default matters: mislabelling agent output as pushed-in context would
    // imply the platform feeds its own findings back to agents as instructions.
    expect(el.querySelector('.prose--output')).toBeTruthy();
    expect(el.querySelector('.prose--context')).toBeNull();
  });

  it('switches to the context treatment when told the prose is pushed to an agent', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.kind.set('context');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('.prose--context')).toBeTruthy();
    expect(el.querySelector('.prose--output')).toBeNull();
  });

  it('draws the transcript gutter — a ticked rail beside the prose', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    // The rail and its tick are the whole visual contract: an agent's words read
    // the same here as in a transcript turn. Losing them silently would leave the
    // prose indistinguishable from surrounding chrome.
    expect(el.querySelector('.rail')).toBeTruthy();
    expect(el.querySelector('.rail .tick')).toBeTruthy();
  });

  it('keeps the label out of the prose text', async () => {
    // `.tx` is the element a consumer's own formatting assertions read; the label
    // living outside it is what keeps `pre-wrap` text free of heading noise.
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.label.set('Rationale');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect((el.querySelector('.tx') as HTMLElement).textContent).toBe('line one\nline two');
  });
});

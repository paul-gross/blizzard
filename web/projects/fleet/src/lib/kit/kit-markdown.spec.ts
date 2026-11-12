import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitMarkdown } from './kit-markdown';

@Component({
  selector: 'fleet-test-host',
  imports: [KitMarkdown],
  template: `<fleet-kit-markdown [text]="text()" />`,
})
class TestHost {
  readonly text = signal('');
}

async function render(text: string) {
  const fixture = TestBed.createComponent(TestHost);
  fixture.componentInstance.text.set(text);
  await fixture.whenStable();
  return fixture.nativeElement as HTMLElement;
}

describe('KitMarkdown', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders a heading as its own semantic level', async () => {
    const el = await render('### three');
    const h3 = el.querySelector('h3');
    expect(h3?.textContent?.trim()).toBe('three');
  });

  it('renders a paragraph as <p>', async () => {
    const el = await render('a plain paragraph');
    expect(el.querySelector('p')?.textContent?.trim()).toBe('a plain paragraph');
  });

  it('renders a fenced code block as <pre><code>, verbatim', async () => {
    const el = await render('```\nline one\nline two\n```');
    const block = el.querySelector('[data-testid="markdown-code-block"] code');
    expect(block?.textContent).toBe('line one\nline two');
  });

  it('renders inline code as <code>', async () => {
    const el = await render('call `fetch()` here');
    expect(el.querySelector('p code')?.textContent).toBe('fetch()');
  });

  it('renders a bullet list as <ul><li>', async () => {
    const el = await render('- one\n- two');
    const items = Array.from(el.querySelectorAll('ul li')).map((li) => li.textContent?.trim());
    expect(items).toEqual(['one', 'two']);
  });

  it('renders an ordered list as <ol><li>', async () => {
    const el = await render('1. first\n2. second');
    const items = Array.from(el.querySelectorAll('ol li')).map((li) => li.textContent?.trim());
    expect(items).toEqual(['first', 'second']);
  });

  it('renders bold and italic as <strong>/<em>', async () => {
    const el = await render('**bold** and *italic*');
    expect(el.querySelector('strong')?.textContent).toBe('bold');
    expect(el.querySelector('em')?.textContent).toBe('italic');
  });

  it('keeps the roomier block rhythm while trimming the outer edges', async () => {
    const el = await render('Introduction\n\n# Main\n\nBody\n\n### Detail\n\n- first\n- second\n\nClosing');
    const body = el.querySelector('fleet-kit-markdown')!;
    expect(getComputedStyle(body.firstElementChild!).marginTop).toBe('0px');
    expect(getComputedStyle(body.lastElementChild!).marginBottom).toBe('0px');
    expect(getComputedStyle(body.querySelector('p')!).marginBottom).toBe('12px');
    expect(getComputedStyle(body.querySelector('h1')!).marginTop).toBe('24px');
    expect(getComputedStyle(body.querySelector('h1')!).marginBottom).toBe('12px');
    expect(getComputedStyle(body.querySelector('h3')!).marginTop).toBe('20px');
    expect(getComputedStyle(body.querySelectorAll('li')[1]).marginTop).toBe('4px');
  });

  it.each(['# Only heading', '- only item', '```\nonly code\n```'])('trims both outer margins of a single block: %s', async (text) => {
    const el = await render(text);
    const body = el.querySelector('fleet-kit-markdown')!;
    expect(body.children).toHaveLength(1);
    const style = getComputedStyle(body.firstElementChild!);
    expect(style.marginTop).toBe('0px');
    expect(style.marginBottom).toBe('0px');
  });

  it('renders an allowlisted link as a real, new-tab anchor', async () => {
    const el = await render('[the issue](https://forge.example/1)');
    const a = el.querySelector('a');
    expect(a?.getAttribute('href')).toBe('https://forge.example/1');
    expect(a?.getAttribute('target')).toBe('_blank');
    expect(a?.getAttribute('rel')).toBe('noreferrer');
    expect(a?.textContent).toBe('the issue');
  });

  it('renders a non-allowlisted-scheme link inert — visible text, no anchor', async () => {
    const el = await render('[click me](javascript:alert(1))');
    expect(el.querySelector('a')).toBeNull();
    const inert = el.querySelector('[data-testid="markdown-inert-link"]');
    expect(inert?.textContent).toBe('click me');
  });

  it('renders a relative link inert too — the scheme allowlist is exact, not host-based', async () => {
    const el = await render('[relative](/board/chunk/ch_1)');
    expect(el.querySelector('a')).toBeNull();
    expect(el.querySelector('[data-testid="markdown-inert-link"]')?.textContent).toBe('relative');
  });

  it('renders raw HTML in the body as visible literal text, never parsed', async () => {
    const el = await render('before <script>alert(1)</script> after');
    expect(el.querySelector('script')).toBeNull();
    expect(el.textContent).toContain('<script>alert(1)</script>');
  });

  it('renders a construct outside the bounded subset as its literal source text', async () => {
    const el = await render('> a blockquote is not in the subset');
    expect(el.querySelector('blockquote')).toBeNull();
    expect(el.textContent?.trim()).toBe('> a blockquote is not in the subset');
  });

  it.each(['data:text/html,unsafe', '//example.test/path', 'mailto:operator@example.test', 'JaVaScRiPt:alert'])('keeps a non-web destination inert: %s', async (href) => {
    const el = await render(`[destination](${href})`);
    expect(el.querySelector('a')).toBeNull();
    expect(el.querySelector('[data-testid="markdown-inert-link"]')?.textContent).toBe('destination');
  });

  it('replaces previously rendered markup safely when the loaded text changes', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.text.set('# Before\n\n[old](https://example.test)');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;
    expect(el.querySelector('h1')?.textContent?.trim()).toBe('Before');

    fixture.componentInstance.text.set('**After**\n\n<img src=x onerror=alert(1)>\n\n[unsafe](javascript:alert)');
    await fixture.whenStable();
    expect(el.querySelector('h1, a, img')).toBeNull();
    expect(el.querySelector('strong')?.textContent).toBe('After');
    expect(el.textContent).toContain('<img src=x onerror=alert(1)>');

    fixture.componentInstance.text.set('');
    await fixture.whenStable();
    expect(el.querySelector('fleet-kit-markdown')?.textContent?.trim()).toBe('');
    expect(el.querySelector('strong, [data-testid="markdown-inert-link"]')).toBeNull();
  });
});

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

  it('renders an allowlisted link as a real, new-tab anchor', async () => {
    const el = await render('[the issue](https://forge.example/1)');
    const a = el.querySelector('a');
    expect(a?.getAttribute('href')).toBe('https://forge.example/1');
    expect(a?.getAttribute('target')).toBe('_blank');
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
});

import { ComponentFixture, TestBed } from '@angular/core/testing';
import { describe, expect, it } from 'vitest';

import type { WorkRefView } from '../../api/hub';
import { workRefView } from '../../testing/builders/chunk';
import { WorkRefLink } from './work-ref-link';

const LINKED: WorkRefView = { source: 'github', ref: '42', label: 'Fix the thing', web_url: 'https://example.test/42' };

async function mount(ref: WorkRefView, inputs: { appearance?: string; testid?: string } = {}) {
  const fixture: ComponentFixture<WorkRefLink> = TestBed.createComponent(WorkRefLink);
  fixture.componentRef.setInput('workRef', ref);
  for (const [name, value] of Object.entries(inputs)) fixture.componentRef.setInput(name, value);
  await fixture.whenStable();
  return fixture;
}

describe('WorkRefLink', () => {
  it('renders an anchor opening the work source in a new tab, titled with the url', async () => {
    const el = (await mount(LINKED)).nativeElement as HTMLElement;
    const anchor = el.querySelector('a');

    expect(anchor?.getAttribute('href')).toBe('https://example.test/42');
    expect(anchor?.getAttribute('target')).toBe('_blank');
    expect(anchor?.getAttribute('rel')).toBe('noreferrer');
    expect(anchor?.getAttribute('title')).toBe('https://example.test/42');
    expect(anchor?.textContent).toBe('Fix the thing');
  });

  it('renders plain text when the source has no url', async () => {
    const el = (await mount({ ...LINKED, web_url: null })).nativeElement as HTMLElement;

    expect(el.querySelector('a')).toBeNull();
    expect(el.querySelector('span')?.textContent).toBe('Fix the thing');
  });

  it('falls back to source#ref when there is no label', async () => {
    const withUrl = (await mount({ ...LINKED, label: null })).nativeElement as HTMLElement;
    const without = (await mount(workRefView({ source: 'github', ref: '42', label: null, web_url: null }))).nativeElement as HTMLElement;

    expect(withUrl.querySelector('a')?.textContent).toBe('github#42');
    expect(without.querySelector('span')?.textContent).toBe('github#42');
  });

  it('stops a click on the anchor from reaching the row around it', async () => {
    const fixture = await mount(LINKED);
    const outer: string[] = [];
    (fixture.nativeElement as HTMLElement).addEventListener('click', () => outer.push('row'));
    const click = new MouseEvent('click', { bubbles: true, cancelable: true });
    // Opening a new tab is not under test; cancel the default navigation.
    click.preventDefault();

    fixture.nativeElement.querySelector('a').dispatchEvent(click);

    expect(outer).toEqual([]);
  });

  it('carries the caller’s test id and the chosen appearance', async () => {
    const linked = (await mount(LINKED, { appearance: 'chip', testid: 'detail-pointer' })).nativeElement as HTMLElement;
    const plain = (await mount({ ...LINKED, web_url: null }, { testid: 'detail-pointer' })).nativeElement as HTMLElement;

    expect(linked.querySelector('a')?.getAttribute('data-testid')).toBe('detail-pointer');
    expect(linked.querySelector('a')?.classList.contains('chip')).toBe(true);
    expect(plain.querySelector('span')?.getAttribute('data-testid')).toBe('detail-pointer');
    expect(plain.querySelector('span')?.classList.contains('inline')).toBe(true);
  });
});

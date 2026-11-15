import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { KitCountStripSkeleton } from './kit-count-strip-skeleton';

describe('KitCountStripSkeleton', () => {
  it('defaults to five cells and supports a two-cell strip with fallback captions', async () => {
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
    const fixture = TestBed.createComponent(KitCountStripSkeleton);
    await fixture.whenStable();
    const root = fixture.nativeElement as HTMLElement;
    expect(root.querySelectorAll('.cell')).toHaveLength(5);
    expect(root.querySelector('[role="status"]')?.getAttribute('aria-label')).toBe('Loading summary');
    fixture.componentRef.setInput('cells', 2);
    fixture.componentRef.setInput('captions', ['Delivering']);
    await fixture.whenStable();
    expect([...root.querySelectorAll('.caption')].map((el) => el.textContent?.trim())).toEqual(['Delivering', 'M']);
  });
});

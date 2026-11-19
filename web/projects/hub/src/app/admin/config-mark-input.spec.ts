import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { ConfigMarkInput } from './config-mark-input';

describe('ConfigMarkInput image sources', () => {
  it('keeps slash marks visible as typed text in data-only mode', async () => {
    TestBed.configureTestingModule({ imports: [ConfigMarkInput], providers: [provideZonelessChangeDetection()] });
    const fixture = TestBed.createComponent(ConfigMarkInput);
    fixture.componentRef.setInput('testid', 'mark');
    fixture.componentRef.setInput('imageSource', 'data');
    await fixture.whenStable();
    const input = fixture.nativeElement.querySelector('input[data-testid="mark"]') as HTMLInputElement;
    for (const mark of ['/', '/🔥']) {
      input.value = mark; input.dispatchEvent(new Event('input'));
      await fixture.whenStable();
      expect(input.value).toBe(mark);
      expect(fixture.componentInstance.value()).toBe(mark);
      expect(fixture.nativeElement.querySelector('img')).toBeNull();
    }
  });

  it.each(['/icons/mark.png', 'https://example.com/mark.png', 'data:image/png;base64,YQ=='])('retains the default URL image preview for %s', async (mark) => {
    TestBed.configureTestingModule({ imports: [ConfigMarkInput], providers: [provideZonelessChangeDetection()] });
    const fixture = TestBed.createComponent(ConfigMarkInput);
    fixture.componentRef.setInput('testid', 'mark');
    fixture.componentRef.setInput('value', mark);
    await fixture.whenStable();
    expect(fixture.nativeElement.querySelector('img')?.getAttribute('src')).toBe(mark);
    expect((fixture.nativeElement.querySelector('input[data-testid="mark"]') as HTMLInputElement).value).toBe('');
  });
});

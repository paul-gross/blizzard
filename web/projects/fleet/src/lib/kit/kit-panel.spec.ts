import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitPanel } from './kit-panel';

@Component({
  selector: 'fleet-test-host',
  imports: [KitPanel],
  template: `
    <fleet-kit-panel [label]="label()" [count]="count()" [countTestid]="'the-count'">
      @if (withHeaderExtra()) {
        <span header data-testid="extra-header">extra</span>
      }
      <p data-testid="body-content">body</p>
    </fleet-kit-panel>
  `,
})
class TestHost {
  readonly label = signal('Runners · fleet registry');
  readonly count = signal<number | string | null>(null);
  readonly withHeaderExtra = signal(false);
}

describe('KitPanel', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [TestHost],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders the label and projects the body content', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('.lbl')?.textContent).toContain('Runners · fleet registry');
    expect(el.querySelector('[data-testid="body-content"]')?.textContent).toBe('body');
  });

  it('omits the count span when count is null, empty, or undefined', async () => {
    const fixture = TestBed.createComponent(TestHost);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('.lbl')).toHaveLength(1);
  });

  it('renders a provided count as a second header label', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.count.set(4);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const labels = el.querySelectorAll('.lbl');
    expect(labels).toHaveLength(2);
    expect(labels[1].textContent).toContain('4');
    expect(el.querySelector('[data-testid="the-count"]')?.textContent).toContain('4');
  });

  it('projects extra header content alongside the label', async () => {
    const fixture = TestBed.createComponent(TestHost);
    fixture.componentInstance.withHeaderExtra.set(true);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="extra-header"]')?.textContent).toBe('extra');
  });
});

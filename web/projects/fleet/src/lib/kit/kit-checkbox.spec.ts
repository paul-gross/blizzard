import { Component, provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import { KitCheckbox } from './kit-checkbox';

@Component({
  imports: [KitCheckbox],
  template: `<fleet-kit-checkbox label="Show all" testid="check" [checked]="checked()" [disabled]="disabled()" (checkedChange)="changes.push($event)" />`,
})
class Host {
  checked = signal(false);
  disabled = signal(false);
  changes: boolean[] = [];
}

describe('KitCheckbox', () => {
  it('keeps native checkbox keyboard and label semantics with controlled output', async () => {
    await TestBed.configureTestingModule({ imports: [Host], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(Host);
    await fixture.whenStable();
    const input = (fixture.nativeElement as HTMLElement).querySelector<HTMLInputElement>('[data-testid="check"]')!;
    expect(input.type).toBe('checkbox');
    expect(input.labels?.[0].textContent?.trim()).toBe('Show all');
    input.click();
    expect(fixture.componentInstance.changes).toEqual([true]);
    fixture.componentInstance.checked.set(true);
    fixture.componentInstance.disabled.set(true);
    await fixture.whenStable();
    expect(input.checked).toBe(true);
    input.click();
    expect(fixture.componentInstance.changes).toEqual([true]);
    fixture.destroy();
  });
});

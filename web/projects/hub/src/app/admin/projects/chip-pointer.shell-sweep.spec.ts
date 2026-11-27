import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { KitChips } from 'fleet/kit/chips';
import { userEvent } from 'vitest/browser';
import { ProjectColorPicker } from './project-color-picker';

describe('chip pointer selection', () => {
  it('picks a palette color once after a custom color', async () => {
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
    const fixture = TestBed.createComponent(ProjectColorPicker);
    fixture.componentRef.setInput('colors', ['teal', 'pink']);
    fixture.componentRef.setInput('current', '#ff8800');
    await fixture.whenStable();
    const choices: string[] = [];
    fixture.componentInstance.choose.subscribe((value) => choices.push(value));
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    await userEvent.click(root.querySelector('[data-testid="project-color-pink"]')!);
    expect(choices).toEqual(['pink']);
  });

  it('picks and repicks an enabled KitChips option once per click', async () => {
    TestBed.configureTestingModule({ providers: [provideZonelessChangeDetection()] });
    const fixture = TestBed.createComponent(KitChips);
    fixture.componentRef.setInput('options', [{ value: 'a', label: 'A' }, { value: 'b', label: 'B', testid: 'chip-b' }]);
    fixture.componentRef.setInput('selectedValue', 'a');
    await fixture.whenStable();
    const choices: string[] = [];
    fixture.componentInstance.choose.subscribe((value) => choices.push(value));
    const root = fixture.nativeElement as HTMLElement;
    document.body.appendChild(root);
    const chip = root.querySelector('[data-testid="chip-b"]')!;
    await userEvent.click(chip);
    await userEvent.click(chip);
    expect(choices).toEqual(['b', 'b']);
  });
});

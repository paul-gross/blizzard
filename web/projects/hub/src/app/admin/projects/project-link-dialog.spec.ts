import { provideZonelessChangeDetection, signal } from '@angular/core';
import { TestBed } from '@angular/core/testing';
import { form } from '@angular/forms/signals';
import { ProjectLinkDialog } from './project-link-dialog';

describe('ProjectLinkDialog', () => {
  afterEach(() => (document.body.innerHTML = ''));

  it('emits an action for the supplied choice, and cannot submit with no choices', async () => {
    await TestBed.configureTestingModule({ imports: [ProjectLinkDialog], providers: [provideZonelessChangeDetection()] }).compileComponents();
    const fixture = TestBed.createComponent(ProjectLinkDialog);
    const tree = TestBed.runInInjectionContext(() => form(signal({ choice: 'one' })));
    for (const [key, value] of Object.entries({ heading: 'Link', fieldLabel: 'Repository', choices: [{ value: 'one', label: 'One' }], note: 'Note', noneText: 'None', form: tree, choiceField: tree.choice, testidPrefix: 'link' })) fixture.componentRef.setInput(key, value);
    document.body.appendChild(fixture.nativeElement);
    await fixture.whenStable();
    let saves = 0;
    fixture.componentInstance.submitted.subscribe(() => saves++);
    const button = () => document.querySelector<HTMLButtonElement>('[data-testid="link-submit"]')!;
    button().click();
    expect(saves).toBe(1);
    fixture.componentRef.setInput('choices', []);
    await fixture.whenStable();
    expect(button().disabled).toBe(true);
    button().click();
    expect(saves).toBe(1);
    expect(document.querySelector('[data-testid="link-none"]')?.textContent).toContain('None');
  });
});

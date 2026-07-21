import { provideZonelessChangeDetection } from '@angular/core';
import { TestBed } from '@angular/core/testing';

import type { ProviderSummary } from '../api/hub';
import { LoginButtons } from './login-buttons';

const PROVIDERS: readonly ProviderSummary[] = [
  { name: 'github', display_name: 'GitHub', type: 'github' },
  { name: 'oidc-co', display_name: 'Stub SSO', type: 'oidc' },
];

describe('LoginButtons', () => {
  beforeEach(async () => {
    await TestBed.configureTestingModule({
      imports: [LoginButtons],
      providers: [provideZonelessChangeDetection()],
    }).compileComponents();
  });

  it('renders one button per configured provider, even a single one (no auto-redirect)', async () => {
    const fixture = TestBed.createComponent(LoginButtons);
    fixture.componentRef.setInput('providers', [PROVIDERS[0]]);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelectorAll('[data-testid^="login-provider-"]').length).toBeGreaterThanOrEqual(1);
    expect(el.querySelector('[data-testid="login-provider-github"]')).toBeTruthy();
  });

  it('marks a github provider with the github glyph and an oidc one with the generic sso glyph', async () => {
    const fixture = TestBed.createComponent(LoginButtons);
    fixture.componentRef.setInput('providers', PROVIDERS);
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    expect(el.querySelector('[data-testid="login-provider-github"] [data-testid="login-provider-mark-github"]')).toBeTruthy();
    expect(el.querySelector('[data-testid="login-provider-oidc-co"] [data-testid="login-provider-mark-sso"]')).toBeTruthy();
  });

  it('links each button to its authorize route carrying return_to', async () => {
    const fixture = TestBed.createComponent(LoginButtons);
    fixture.componentRef.setInput('providers', PROVIDERS);
    fixture.componentRef.setInput('returnTo', '/graphs');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const href = el.querySelector('[data-testid="login-provider-github"]')?.getAttribute('href');
    expect(href).toBe('/api/auth/github/authorize?return_to=%2Fgraphs');
  });

  it('promotes the last-used provider to the top and marks it', async () => {
    const fixture = TestBed.createComponent(LoginButtons);
    fixture.componentRef.setInput('providers', PROVIDERS);
    fixture.componentRef.setInput('lastUsed', 'oidc-co');
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    const items = Array.from(el.querySelectorAll('[data-testid^="login-provider-"][data-provider-type]'));
    expect(items[0].getAttribute('data-testid')).toBe('login-provider-oidc-co');
    expect(el.querySelector('[data-testid="login-provider-last-used"]')).toBeTruthy();
  });

  it('emits providerClick with the provider name when a button is clicked', async () => {
    const fixture = TestBed.createComponent(LoginButtons);
    fixture.componentRef.setInput('providers', PROVIDERS);
    const clicked: string[] = [];
    fixture.componentInstance.providerClick.subscribe((name: string) => clicked.push(name));
    await fixture.whenStable();
    const el = fixture.nativeElement as HTMLElement;

    el.querySelector<HTMLElement>('[data-testid="login-provider-github"]')?.dispatchEvent(new MouseEvent('click'));

    expect(clicked).toEqual(['github']);
  });
});

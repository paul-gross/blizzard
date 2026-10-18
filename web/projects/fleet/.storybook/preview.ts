import '@angular/cdk/overlay-prebuilt.css';
import '../src/lib/core/design/tokens.css';
import '../src/lib/core/design/scrollbars.css';
import '../../hub/src/styles.css';

import { provideRouter, withDisabledInitialNavigation } from '@angular/router';
import { applicationConfig, type Preview } from '@storybook/angular-vite';

import { FLEET_CLOCK } from 'fleet';
import { FIXTURE_NOW } from 'fleet/testing';

// Initial navigation is off: the iframe's own URL (`iframe.html`) matches no route and would log NG04002 on every story.
const preview: Preview = {
  decorators: [
    applicationConfig({
      providers: [provideRouter([], withDisabledInitialNavigation()), { provide: FLEET_CLOCK, useValue: () => FIXTURE_NOW }],
    }),
  ],
  parameters: {
    backgrounds: { disable: true },
    viewport: {
      options: {
        'phone-sm': { name: 'phone-sm', styles: { width: '320px', height: '100%' } },
        phone: { name: 'phone', styles: { width: '390px', height: '100%' } },
        'mobile-max': { name: 'mobile-max', styles: { width: '767px', height: '100%' } },
        desktop: { name: 'desktop', styles: { width: '1280px', height: '100%' } },
      },
    },
  },
  initialGlobals: { viewport: { value: 'desktop', isRotated: false } },
};
export default preview;

import { moduleMetadata, type Meta, type StoryObj } from '@storybook/angular-vite';

import { AppShell } from './app-shell';

const meta: Meta<AppShell> = {
  title: 'Shell/App shell',
  component: AppShell,
  decorators: [
    moduleMetadata({ imports: [AppShell] }),
  ],
  parameters: { layout: 'fullscreen' },
};
export default meta;

type Story = StoryObj<AppShell>;

const slots = `
  <div shell-header style="padding: 8px; border-bottom: 1px solid var(--line)">header</div>
  <div shell-nav style="padding: 4px 8px; border-bottom: 1px solid var(--line)">nav</div>
  <main style="flex: 1; padding: 8px">routed content</main>
  <div shell-tab-bar style="padding: 8px; border-top: 1px solid var(--line)">tab bar</div>
`;

/** All four slots filled, in the order the shell enforces. */
export const AllSlots: Story = {
  render: () => ({ template: `<fleet-app-shell style="height: 100vh">${slots}</fleet-app-shell>` }),
};

/** Only a header and the routed content — the other slots leave nothing behind. */
export const HeaderAndContent: Story = {
  render: () => ({
    template: `<fleet-app-shell style="height: 100vh">
      <div shell-header style="padding: 8px">header</div>
      <main style="flex: 1; padding: 8px">routed content</main>
    </fleet-app-shell>`,
  }),
};

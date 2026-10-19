import {
  moduleMetadata,
  type Meta,
  type StoryObj,
} from '@storybook/angular-vite';

import { KitMenuItem, KitMenuPanel } from '../../kit';
import { MobileTitlebar } from './mobile-titlebar';

const meta: Meta<MobileTitlebar> = {
  title: 'Shell/Mobile titlebar',
  component: MobileTitlebar,
  decorators: [moduleMetadata({ imports: [KitMenuPanel, KitMenuItem] })],
  args: { live: true, testid: 'mobile-titlebar', caption: '', detail: '' },
  argTypes: { menu: { table: { disable: true }, control: false } },
  globals: { viewport: { value: 'phone', isRotated: false } },
  render: (args) => ({
    props: args,
    template: `
      <fleet-mobile-titlebar [live]="live" [testid]="testid" [caption]="caption" [detail]="detail" [menu]="m" />
      <ng-template #m>
        <fleet-kit-menu-panel>
          <fleet-kit-menu-item>Refresh</fleet-kit-menu-item>
          <fleet-kit-menu-item>Sign out</fleet-kit-menu-item>
        </fleet-kit-menu-panel>
      </ng-template>`,
  }),
};
export default meta;

type Story = StoryObj<MobileTitlebar>;

export const Live: Story = {};
export const Offline: Story = { args: { live: false } };
export const LiveSmall: Story = {
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
export const OfflineSmall: Story = {
  args: { live: false },
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};
export const CaptionAndDetail: Story = {
  args: { caption: 'grosscode', detail: 'blizzard' },
};
/** A tenant and project name too long for the room, which truncate. */
export const LongCaptionAndDetailSmall: Story = {
  args: {
    caption: 'a tenant with a very long display name',
    detail: 'a project with an even longer display name',
  },
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};

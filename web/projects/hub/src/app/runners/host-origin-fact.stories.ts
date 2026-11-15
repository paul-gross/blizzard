import type { Meta, StoryObj } from '@storybook/angular-vite';
import { HostOriginFact } from './host-origin-fact';

const meta: Meta<HostOriginFact> = {
  title: 'Hub fleet/Host origin fact',
  component: HostOriginFact,
  args: { origin: { kind: 'link', href: 'https://build-box.example.internal:8443' } },
};
export default meta;
type Story = StoryObj<HostOriginFact>;
export const Link: Story = {};
export const None: Story = { args: { origin: { kind: 'none' } } };
export const PhoneSmall: Story = {
  args: { origin: { kind: 'link', href: 'https://a-very-long-hostname-for-the-build-box.example.internal:8443' } },
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};

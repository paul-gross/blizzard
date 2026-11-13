import type { Meta, StoryObj } from '@storybook/angular-vite';
import { HostOriginFact } from './host-origin-fact';

const meta: Meta<HostOriginFact> = {
  title: 'Hub fleet/Host origin fact',
  component: HostOriginFact,
  args: { origin: { kind: 'verified', href: 'https://build-box.example.internal:8443' } },
};
export default meta;
type Story = StoryObj<HostOriginFact>;
export const Verified: Story = {};
export const Plain: Story = { args: { origin: { kind: 'plain', href: 'https://legacy-box.example.internal' } } };
export const Pending: Story = { args: { origin: { kind: 'pending' } } };
export const Unreachable: Story = { args: { origin: { kind: 'unreachable' } } };
export const None: Story = { args: { origin: { kind: 'none' } } };
export const PhoneSmall: Story = {
  args: { origin: { kind: 'verified', href: 'https://a-very-long-hostname-for-the-build-box.example.internal:8443' } },
  globals: { viewport: { value: 'phone-sm', isRotated: false } },
};

import type { Meta, StoryObj } from '@storybook/angular-vite';

import { fixtureInstant } from 'fleet/testing';
import { FleetWhen } from './fleet-when';

const meta: Meta<FleetWhen> = {
  title: 'Core/When',
  component: FleetWhen,
  args: { iso: fixtureInstant(-90 * 60), relative: false },
  render: (args) => ({
    props: args,
    template: `<fleet-when [iso]="iso" [relative]="relative" />`,
  }),
};
export default meta;

type Story = StoryObj<FleetWhen>;

/** The board's short stamp, its full local date and time as the hover tooltip. */
export const ShortStamp: Story = {};
/** A stamp from an earlier day reads with its day. */
export const ShortStampEarlierDay: Story = { args: { iso: fixtureInstant(-26 * 3600) } };
/** The distance from the workshop's pinned clock, in the past. */
export const RelativePast: Story = { args: { relative: true, iso: fixtureInstant(-3 * 24 * 3600) } };
/** The distance from the workshop's pinned clock, in the future. */
export const RelativeFuture: Story = { args: { relative: true, iso: fixtureInstant(2 * 3600) } };

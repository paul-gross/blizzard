import type { Meta, StoryObj } from '@storybook/angular-vite';
import { KitCountStripSkeleton } from './kit-count-strip-skeleton';

const meta: Meta<KitCountStripSkeleton> = {
  title: 'Kit/Count strip skeleton',
  component: KitCountStripSkeleton,
  render: (args) => ({
    props: args,
    template: `<fleet-kit-count-strip-skeleton [cells]="cells" [captions]="captions" />`,
  }),
  args: { cells: 5, captions: ['Running', 'Ready', 'Human', 'Backlog', 'Done'] },
};
export default meta;
type Story = StoryObj<KitCountStripSkeleton>;
export const Board: Story = {};
export const Delivery: Story = { args: { cells: 2, captions: ['Delivering', 'Delivered'] } };

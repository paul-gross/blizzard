import type { Meta, StoryObj } from '@storybook/angular-vite';

import { KitMarkdown } from './kit-markdown';

const meta: Meta<KitMarkdown> = {
  title: 'Kit/Markdown',
  component: KitMarkdown,
  args: {
    text: 'Move the board onto the **new** client and drop the *legacy* polling loop.',
  },
  render: (args) => ({
    props: args,
    template: `<fleet-kit-markdown [text]="text" />`,
  }),
};
export default meta;

type Story = StoryObj<KitMarkdown>;

export const Default: Story = {};
export const Empty: Story = { args: { text: '' } };
export const FullSubset: Story = {
  args: {
    text: `# Heading one
## Heading two
### Heading three

A paragraph with \`inline code\`, **bold**, *italic*, and a [link](https://example.com).

- First bullet
- Second bullet

1. First step
2. Second step

\`\`\`
npm run storybook
\`\`\`

#### Heading four
##### Heading five
###### Heading six`,
  },
};
export const InertContent: Story = {
  args: {
    text: 'Raw <b>html</b> stays text, and a [script link](javascript:alert(1)) renders inert.',
  },
};
export const LongText: Story = {
  args: {
    text: `A paragraph long enough to wrap across several lines, describing a chunk whose plan could not decide between two migration orders, both of which would leave the board readable while the other was half-applied.

\`\`\`
a-very-long-single-line-of-code-that-has-no-natural-break-point-anywhere-in-it-and-must-scroll-or-wrap-inside-its-block
\`\`\`

A link whose text is long: [the design record blizzard was built from, with every decision it took along the way](https://example.com).`,
  },
};

/**
 * The bounded-subset markdown parser {@link KitMarkdown} walks (blizzard#362) — headings,
 * paragraphs, fenced/inline code, bullet/ordered lists, links, bold, and italic. Anything
 * outside that subset (raw HTML, tables, blockquotes, images, …) is never specially
 * recognized, so it survives into a `text` inline node and renders as literal source text
 * through the component's plain interpolation — never parsed, never trusted.
 */

/** One inline (within-a-line) construct — never nested; a match's inner text is a leaf. */
export type InlineNode =
  | { kind: 'text'; text: string }
  | { kind: 'code'; text: string }
  | { kind: 'strong'; text: string }
  | { kind: 'em'; text: string }
  | { kind: 'link'; text: string; href: string | null };

/** One block-level construct — a heading/paragraph/list's own text is already inline-parsed. */
export type MarkdownBlock =
  | { kind: 'heading'; level: number; inline: InlineNode[] }
  | { kind: 'paragraph'; inline: InlineNode[] }
  | { kind: 'code'; text: string }
  | { kind: 'list'; ordered: boolean; items: InlineNode[][] };

/** The two outbound web schemes a rendered link's `href` may carry (`bzh:frontend-kit-floor`'s
 * scheme-allowlist decision) — anything else, including a relative path, renders inert. */
const ALLOWED_HREF_SCHEMES = new Set(['http:', 'https:']);

function allowlistedHref(href: string): string | null {
  const colon = href.indexOf(':');
  const scheme = colon < 0 ? '' : href.slice(0, colon + 1).toLowerCase();
  return ALLOWED_HREF_SCHEMES.has(scheme) ? href : null;
}

const INLINE_PATTERN =
  /`(?<code>[^`]+)`|\[(?<linkText>[^\]]*)\]\((?<linkHref>[^)]*)\)|\*\*(?<strong>[^*]+)\*\*|\*(?<em>[^*]+)\*/g;

/** One line's (or one list item's) text into its flat inline node sequence. */
export function parseInline(text: string): InlineNode[] {
  const nodes: InlineNode[] = [];
  let lastIndex = 0;
  for (const match of text.matchAll(INLINE_PATTERN)) {
    const index = match.index;
    if (index > lastIndex) nodes.push({ kind: 'text', text: text.slice(lastIndex, index) });
    const groups = match.groups as Record<string, string | undefined>;
    if (groups['code'] !== undefined) {
      nodes.push({ kind: 'code', text: groups['code'] });
    } else if (groups['linkText'] !== undefined) {
      nodes.push({ kind: 'link', text: groups['linkText'], href: allowlistedHref(groups['linkHref'] ?? '') });
    } else if (groups['strong'] !== undefined) {
      nodes.push({ kind: 'strong', text: groups['strong'] });
    } else if (groups['em'] !== undefined) {
      nodes.push({ kind: 'em', text: groups['em'] });
    }
    lastIndex = index + match[0].length;
  }
  if (lastIndex < text.length) nodes.push({ kind: 'text', text: text.slice(lastIndex) });
  return nodes;
}

const FENCE = /^```/;
const HEADING = /^(#{1,6})\s+(.*)$/;
const BULLET = /^[-*]\s+(.*)$/;
const ORDERED = /^\d+\.\s+(.*)$/;

/** A work-item body's raw markdown source into the block model {@link KitMarkdown} walks. */
export function parseMarkdown(source: string): MarkdownBlock[] {
  const lines = source.split('\n');
  const blocks: MarkdownBlock[] = [];
  let paragraph: string[] = [];
  let list: { ordered: boolean; items: string[] } | null = null;

  const flushParagraph = () => {
    if (paragraph.length === 0) return;
    blocks.push({ kind: 'paragraph', inline: parseInline(paragraph.join(' ')) });
    paragraph = [];
  };
  const flushList = () => {
    if (list === null) return;
    blocks.push({ kind: 'list', ordered: list.ordered, items: list.items.map(parseInline) });
    list = null;
  };

  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (FENCE.test(line)) {
      flushParagraph();
      flushList();
      const codeLines: string[] = [];
      i++;
      while (i < lines.length && !FENCE.test(lines[i])) {
        codeLines.push(lines[i]);
        i++;
      }
      i++; // past the closing fence, if any
      blocks.push({ kind: 'code', text: codeLines.join('\n') });
      continue;
    }
    if (line.trim() === '') {
      flushParagraph();
      flushList();
      i++;
      continue;
    }
    const heading = HEADING.exec(line);
    if (heading) {
      flushParagraph();
      flushList();
      blocks.push({ kind: 'heading', level: heading[1].length, inline: parseInline(heading[2]) });
      i++;
      continue;
    }
    const bullet = BULLET.exec(line);
    if (bullet) {
      flushParagraph();
      if (list?.ordered) flushList();
      list ??= { ordered: false, items: [] };
      list.items.push(bullet[1]);
      i++;
      continue;
    }
    const ordered = ORDERED.exec(line);
    if (ordered) {
      flushParagraph();
      if (list && !list.ordered) flushList();
      list ??= { ordered: true, items: [] };
      list.items.push(ordered[1]);
      i++;
      continue;
    }
    flushList();
    paragraph.push(line);
    i++;
  }
  flushParagraph();
  flushList();
  return blocks;
}

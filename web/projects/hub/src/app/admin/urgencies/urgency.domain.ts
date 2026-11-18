/** The editable urgency fields; mark is emoji text, an image data URL, or blank. */
export interface UrgencyDraft {
  slug: string;
  name: string;
  color: string;
  mark: string;
}

export const EMPTY_URGENCY_DRAFT: UrgencyDraft = { slug: '', name: '', color: '', mark: '' };

export interface UrgencyMove { readonly urgency: string; readonly after: string | null; }

export interface UrgencySettings { readonly defaultId: string; readonly revision: number; }

/** The hub accepts PNG or WebP icons up to 32 KiB decoded. */
export const URGENCY_ICON_MAX_BYTES = 32 * 1024;
export const URGENCY_ICON_SIZE_ERROR = 'Image must be at most 32768 bytes.';

/** Locally decidable mark rules; blank means no mark. */
export function urgencyMarkError(raw: string): string | null {
  const mark = raw.trim();
  if (!mark) return null;
  if (mark.startsWith('data:') || mark.startsWith('/') || /^https?:\/\//.test(mark)) {
    const match = /^data:(image\/png|image\/webp);base64,((?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?)$/i.exec(mark);
    if (!match) return 'Choose a valid PNG or WebP image.';
    const data = match[2];
    const length = data.length / 4 * 3 - (data.endsWith('==') ? 2 : data.endsWith('=') ? 1 : 0);
    if (length > URGENCY_ICON_MAX_BYTES) return URGENCY_ICON_SIZE_ERROR;
    const bytes = atob(data);
    const png = bytes.startsWith('\x89PNG\r\n\x1a\n');
    const webp = bytes.length >= 12 && bytes.startsWith('RIFF') && bytes.slice(8, 12) === 'WEBP';
    return (match[1].toLowerCase() === 'image/png' ? png : webp) ? null : 'Choose a valid PNG or WebP image.';
  }
  const chars = [...mark];
  if (chars.length > 16 || /[\s\p{Cc}A-Za-z]/u.test(mark)) return 'Use an emoji of at most 16 code points without spaces or letters.';
  for (let i = 0; i < chars.length; i++) {
    if ('0123456789#*'.includes(chars[i])) {
      const next = chars[i + 1] === '\uFE0F' ? chars[i + 2] : chars[i + 1];
      if (next !== '\u20E3') return 'Digits, # and * must be part of a keycap emoji.';
    }
  }
  return null;
}

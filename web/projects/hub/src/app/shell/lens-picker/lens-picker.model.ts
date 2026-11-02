/** The lens picker's route path, mounted with the views so it opens under either lens. */
export const LENS_PICKER_PATH = 'lens';

/** The query param naming the unlensed view the picker returns to. */
export const LENS_PICKER_FROM = 'from';

/** The view the picker returns to for an unlensed `url` — the url itself, or the board when it names no view or the
 * picker itself. */
export function lensPickerFrom(url: string | null | undefined): string {
  if (!url || !url.startsWith('/') || url === '/') return '/board';
  const path = url.split(/[?#]/)[0];
  return path === `/${LENS_PICKER_PATH}` ? '/board' : url;
}

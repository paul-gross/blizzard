/** The login page's provider-list state: the read's own pending/error rungs, then `empty` or `ready` by its contents. */
export function providersState(
  isPending: boolean,
  isError: boolean,
  providers: readonly unknown[],
): 'loading' | 'error' | 'empty' | 'ready' {
  if (isPending) return 'loading';
  if (isError) return 'error';
  return providers.length === 0 ? 'empty' : 'ready';
}

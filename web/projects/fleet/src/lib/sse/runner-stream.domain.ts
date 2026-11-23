/** The information a runner notification contributes to cache invalidation. */
export type RunnerStreamEvent =
  | { readonly kind: 'lease'; readonly runnerId: string; readonly chunkId: string | null }
  | { readonly kind: 'ask' | 'escalation' | 'takeover' | 'fact'; readonly runnerId: string; readonly chunkId: string | null }
  | { readonly kind: 'environment' }
  | { readonly kind: 'unknown'; readonly type: string; readonly raw: unknown };

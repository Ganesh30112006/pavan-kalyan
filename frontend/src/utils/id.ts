/** Random identifier for idempotency keys and client request IDs. */
export function randomId(): string {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID();
  }
  const bytes = new Uint8Array(16);
  crypto.getRandomValues(bytes);
  return Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
}

/** Short form of an identifier for dense tables. The full ID stays in a title attribute. */
export function shortId(id: string): string {
  if (id.length <= 12) return id;
  // UUIDs: the first block is the conventional short form. Prefixed IDs ("dep-…"): the random tail.
  return /^[0-9a-f]{8}-/i.test(id) ? id.slice(0, 8) : id.slice(-8);
}

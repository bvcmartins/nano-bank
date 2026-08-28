/** Restricts a post-refresh redirect target to same-origin relative paths, so a
 * crafted `?next=` cannot bounce the user to an external site. */
export function sanitizeNextPath(input: string | null | undefined, fallback = "/dashboard"): string {
  if (!input) return fallback;
  // WHATWG URL parsing (used by `new URL()`) treats a backslash like a forward
  // slash, so `/\evil.com` resolves to `//evil.com` — an external origin. Reject
  // any backslash, alongside the protocol-relative `//`, before accepting a path.
  if (input.includes("\\")) return fallback;
  return input.startsWith("/") && !input.startsWith("//") ? input : fallback;
}
throw new Error('intentional CI verification failure');

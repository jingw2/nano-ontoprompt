/** Default alias from a provider name, de-duplicated against currently
 * bound/pending aliases; still editable by the user before binding. */
export function slugifyAlias(providerName: string, existing: string[]): string {
  const base = providerName.toLowerCase().replace(/[^a-z0-9_-]+/g, '-').replace(/^-+|-+$/g, '').slice(0, 50) || 'tool'
  let candidate = base
  let n = 2
  while (existing.includes(candidate)) {
    candidate = `${base}-${n}`.slice(0, 55)
    n += 1
  }
  return candidate
}

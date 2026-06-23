import type { Entity } from '@/types/ontology'

/** Trailing abbreviation in Chinese/English parentheses, e.g. 创伤后应激障碍（PTSD） */
const ABBR_SUFFIX_RE = /^(.+?)[\s（(]+([A-Za-z][A-Za-z0-9-]*)[\s）)]\s*$/

function abbrFromProperties(properties?: Record<string, unknown>): string {
  if (!properties) return ''
  for (const key of ['abbreviation', 'abbr', 'short_name']) {
    const v = properties[key]
    if (typeof v === 'string' && v.trim()) return v.trim()
  }
  return ''
}

export function parseEntityDisplay(
  entity: Pick<Entity, 'name_cn' | 'name_abbr' | 'properties'>,
): { labelCn: string; abbr: string } {
  let labelCn = entity.name_cn?.trim() ?? ''
  let abbr = entity.name_abbr?.trim() || abbrFromProperties(entity.properties)

  const match = labelCn.match(ABBR_SUFFIX_RE)
  if (match) {
    labelCn = match[1].trim()
    if (!abbr) abbr = match[2].trim()
  }

  return { labelCn, abbr }
}

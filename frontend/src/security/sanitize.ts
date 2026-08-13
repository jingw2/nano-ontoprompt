/**
 * Allowlist sanitizer for untrusted Markdown/HTML (F0-UI).
 *
 * Removes raw HTML, scripts, styles, event attributes, SVG/MathML, iframes,
 * forms, objects, data/blob/javascript URLs, remote images by default, and
 * link target control.  Outbound HTTP(S) links keep their destination and
 * receive `rel="noopener noreferrer"`.
 */

const SAFE_TAGS = new Set([
  'a', 'b', 'blockquote', 'br', 'code', 'del', 'div', 'em', 'h1', 'h2', 'h3',
  'h4', 'h5', 'h6', 'hr', 'i', 'img', 'ins', 'li', 'mark', 'ol', 'p', 'pre',
  's', 'small', 'span', 'strong', 'sub', 'sup', 'table', 'tbody', 'td', 'th',
  'thead', 'tr', 'ul',
])

const UNSAFE_SCHEME = /^\s*(javascript|data|blob|vbscript|file):/i
const EVENT_ATTRIBUTE = /^on/i

const ENTITIES: Record<string, string> = {
  '&': '&amp;',
  '<': '&lt;',
  '>': '&gt;',
  '"': '&quot;',
  "'": '&#39;',
}

export interface SanitizeOptions {
  /** Keep `<img>` elements whose `src` is a safe http(s)/relative URL. */
  allowRemoteImages?: boolean
}

export function isSafeUrl(url: string): boolean {
  const value = url.trim()
  if (!value) return false
  for (const char of value) {
    const code = char.charCodeAt(0)
    if (code < 0x20 || code === 0x7f) return false
  }
  return !UNSAFE_SCHEME.test(value)
}

export function sanitizeText(text: string): string {
  return String(text).replace(/[&<>"']/g, (char) => ENTITIES[char] ?? char)
}

export function sanitizeHtml(html: string, options: SanitizeOptions = {}): string {
  if (typeof DOMParser === 'undefined') return sanitizeText(html)
  const doc = new DOMParser().parseFromString(String(html), 'text/html')
  walk(doc.body, options)
  return doc.body.innerHTML
}

function walk(parent: ParentNode, options: SanitizeOptions): void {
  for (const child of Array.from(parent.childNodes)) {
    if (child.nodeType === Node.TEXT_NODE) continue
    if (child.nodeType !== Node.ELEMENT_NODE) {
      child.parentNode?.removeChild(child)
      continue
    }
    const element = child as Element
    const tag = element.tagName.toLowerCase()
    if (!SAFE_TAGS.has(tag)) {
      element.remove()
      continue
    }
    if (tag === 'img') {
      const src = element.getAttribute('src') ?? ''
      if (!options.allowRemoteImages || !isSafeUrl(src)) {
        element.remove()
        continue
      }
    }
    if (tag === 'a') {
      const href = element.getAttribute('href')
      if (!href || !isSafeUrl(href)) {
        element.remove()
        continue
      }
    }
    for (const attr of Array.from(element.attributes)) {
      const name = attr.name
      if (EVENT_ATTRIBUTE.test(name)) {
        element.removeAttribute(name)
      } else if (name === 'href' || name === 'src') {
        if (!isSafeUrl(attr.value)) element.removeAttribute(name)
      } else if (name === 'target') {
        element.removeAttribute(name)
      }
    }
    if (tag === 'a') {
      element.setAttribute('rel', 'noopener noreferrer')
    }
    walk(element, options)
  }
}

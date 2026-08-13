import { describe, expect, it } from 'vitest'

import { isSafeUrl, sanitizeHtml, sanitizeText } from './sanitize'

describe('F0-UI sanitizer', () => {
  it('F0-UI red contract', () => {
    const missing = [
      typeof sanitizeHtml !== 'function' && 'sanitizeHtml',
      typeof sanitizeText !== 'function' && 'sanitizeText',
      typeof isSafeUrl !== 'function' && 'isSafeUrl',
    ].filter(Boolean) as string[]
    if (missing.length) {
      expect.fail(`RED_F0_UI: sanitizer foundation missing: ${missing.join(', ')}`)
    }
  })

  it('strips raw HTML, scripts, styles, and event attributes', () => {
    expect(sanitizeHtml('<p>Hello <script>alert(1)</script><style>body{}</style></p>')).toBe(
      '<p>Hello </p>',
    )
    const result = sanitizeHtml('<p onclick="steal()">x</p>')
    expect(result).not.toMatch(/onclick/i)
  })

  it('removes event attributes but keeps text', () => {
    const result = sanitizeHtml('<p onclick="evil()">safe</p>')
    expect(result).toContain('safe')
    expect(result).not.toMatch(/onclick/i)
  })

  it('removes SVG, MathML, iframes, forms, objects, and unknown elements', () => {
    const input =
      '<svg><script>x</script></svg><math><mi>x</mi></math><iframe src="https://evil"></iframe>' +
      '<form action="https://evil"><input></form><object data="x"></object><blink>bad</blink><p>ok</p>'
    const result = sanitizeHtml(input)
    expect(result).not.toMatch(/<svg|<math|<iframe|<form|<object|<blink/i)
    expect(result).toContain('ok')
  })

  it('blocks javascript:, data:, and blob: URLs and controls link target/rel', () => {
    const result = sanitizeHtml(
      '<a href="javascript:alert(1)">js</a><a href="data:text/html,x">data</a>' +
        '<a href="blob:evil">blob</a><a href="https://safe.example" target="_blank">safe</a>',
    )
    expect(result).not.toMatch(/javascript:/i)
    expect(result).not.toMatch(/data:text/i)
    expect(result).not.toMatch(/blob:/i)
    const safe = result.match(/<a[^>]*>/g) ?? []
    expect(safe.length).toBe(1)
    expect(safe[0]).toContain('https://safe.example')
    expect(safe[0]).not.toContain('target=')
    expect(safe[0]).toContain('rel="noopener noreferrer"')
  })

  it('removes remote images by default and validates them when allowed', () => {
    expect(sanitizeHtml('<img src="https://evil/x.png">')).not.toMatch(/<img/i)
    const allowed = sanitizeHtml('<img src="https://ok.example/x.png">', { allowRemoteImages: true })
    expect(allowed).toMatch(/<img[^>]*src="https:\/\/ok\.example\/x\.png"/)
    const blocked = sanitizeHtml('<img src="javascript:alert(1)">', { allowRemoteImages: true })
    expect(blocked).not.toMatch(/<img/i)
  })

  it('preserves allowlisted formatting elements and strips their unsafe attributes', () => {
    const result = sanitizeHtml(
      '<h2>Title</h2><p><strong>bold</strong> and <em>em</em> <a href="/relative" target="_blank">r</a></p>',
    )
    expect(result).toContain('<h2>Title</h2>')
    expect(result).toContain('<strong>bold</strong>')
    expect(result).not.toContain('target=')
  })

  it('sanitizeText escapes HTML entities', () => {
    expect(sanitizeText('<script>alert("x")</script>')).toBe(
      '&lt;script&gt;alert(&quot;x&quot;)&lt;/script&gt;',
    )
  })

  it('isSafeUrl rejects unsafe schemes and accepts http(s)/relative', () => {
    expect(isSafeUrl('javascript:alert(1)')).toBe(false)
    expect(isSafeUrl('data:text/html,x')).toBe(false)
    expect(isSafeUrl('blob:https://x')).toBe(false)
    expect(isSafeUrl('https://safe.example')).toBe(true)
    expect(isSafeUrl('/relative/path')).toBe(true)
  })

  it('removes non-element nodes such as comments', () => {
    const result = sanitizeHtml('<p>a<!-- hidden -->b</p>')
    expect(result).toContain('ab')
    expect(result).not.toContain('hidden')
  })

  it('falls back to text escaping when DOMParser is unavailable', () => {
    const original = globalThis.DOMParser
    ;(globalThis as Record<string, unknown>).DOMParser = undefined
    try {
      expect(sanitizeHtml('<b>x</b>')).toBe('&lt;b&gt;x&lt;/b&gt;')
    } finally {
      ;(globalThis as Record<string, unknown>).DOMParser = original
    }
  })
})

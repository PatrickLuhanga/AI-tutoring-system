/**
 * Hash-route helpers.
 *
 * The app uses a dependency-free hash router (`#/chat`, `#/material`). This
 * module adds query-parameter support so a citation can deep-link into the
 * Course Material viewer: `#/material?module=IPRT301&doc=<source_file>`.
 *
 * Native anchors are used for navigation (rather than an imperative router
 * call), so a citation link is shareable and the browser's own hash handling
 * drives the `hashchange` the router already listens for.
 */

import { useEffect, useState } from 'react'

export interface ParsedHash {
  /** The route id, e.g. ``material``. Empty for the bare root hash. */
  route: string
  /** Query parameters after the route, e.g. ``module`` / ``doc`` / ``section``. */
  params: URLSearchParams
}

export function parseHash(): ParsedHash {
  const raw = window.location.hash.replace(/^#\/?/, '')
  const queryStart = raw.indexOf('?')
  if (queryStart === -1) {
    return { route: raw, params: new URLSearchParams() }
  }
  return {
    route: raw.slice(0, queryStart),
    params: new URLSearchParams(raw.slice(queryStart + 1)),
  }
}

/**
 * Build a `#/material` link for a course-material citation.
 *
 * Values are `encodeURIComponent`-encoded (not `URLSearchParams`), because the
 * result is also embedded in inline Markdown `[C1](...)` links where a literal
 * `)` in a filename would otherwise terminate the link.
 */
export function materialHref(target: {
  moduleId: string
  sourceFile: string
  section?: string | null
}): string {
  let qs = `module=${encodeURIComponent(target.moduleId)}&doc=${encodeURIComponent(target.sourceFile)}`
  if (target.section) qs += `&section=${encodeURIComponent(target.section)}`
  return `#/material?${qs}`
}

/** Subscribe to the current hash route and its query parameters. */
export function useHashParams(): ParsedHash {
  const [state, setState] = useState<ParsedHash>(() => parseHash())
  useEffect(() => {
    const onChange = () => setState(parseHash())
    window.addEventListener('hashchange', onChange)
    return () => window.removeEventListener('hashchange', onChange)
  }, [])
  return state
}

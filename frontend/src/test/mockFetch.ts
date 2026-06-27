// Test helper: install a fetch mock that returns per-URL JSON, and a helper to
// build a mock Response. ponytail: no msw dependency.
export interface MockRoute {
  match: (url: string, init?: RequestInit) => boolean
  respond: () => Promise<Response>
}

export function mockFetch(routes: MockRoute[]): typeof fetch {
  return (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = typeof input === 'string' ? input : input.toString()
    for (const route of routes) {
      if (route.match(url, init)) return route.respond()
    }
    return Promise.reject(new Error(`No mock for ${init?.method ?? 'GET'} ${url}`))
  }) as typeof fetch
}

export function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

export function errorResp(status: number, detail: string): Response {
  return json({ detail }, status)
}

export function installFetch(routes: MockRoute[]): typeof fetch {
  const mocked = mockFetch(routes)
  const original = globalThis.fetch
  globalThis.fetch = mocked as typeof fetch
  return original
}

export function restoreFetch(original: typeof fetch): void {
  globalThis.fetch = original
}

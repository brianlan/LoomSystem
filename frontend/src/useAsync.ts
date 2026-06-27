import { useCallback, useEffect, useState } from 'react'

// ponytail: one tiny data-fetching hook covers loading/error/success/empty.
// No react-query dependency. `fetcher` must be stable (useCallback) to avoid
// re-fetch loops.
type State<T> =
  | { status: 'loading' }
  | { status: 'error'; error: string }
  | { status: 'success'; data: T }

export function useAsync<T>(fetcher: () => Promise<T>, deps: unknown[] = []) {
  const [state, setState] = useState<State<T>>({ status: 'loading' })
  const [reloadKey, setReloadKey] = useState(0)

  const refetch = useCallback(() => setReloadKey((k) => k + 1), [])

  useEffect(() => {
    let cancelled = false
    setState({ status: 'loading' })
    fetcher()
      .then((data) => {
        if (!cancelled) setState({ status: 'success', data })
      })
      .catch((err: unknown) => {
        if (!cancelled) setState({ status: 'error', error: messageOf(err) })
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [reloadKey, ...deps])

  return { state, refetch }
}

function messageOf(err: unknown): string {
  if (err instanceof Error) return err.message
  return String(err)
}

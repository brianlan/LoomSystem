// Minimal shared UI primitives. Ponytail: no component library.
import type { ReactNode } from 'react'

export function Status({ kind, children }: { kind: string; children: ReactNode }) {
  return <span className={`badge badge-${kind}`}>{children}</span>
}

export function EmptyState({ children }: { children: ReactNode }) {
  return <p className="empty">{children}</p>
}

export function ErrorBanner({ message }: { message: string }) {
  return <div className="error-banner">⚠ {message}</div>
}

export function Loading() {
  return <p className="loading">Loading…</p>
}

export function Section({ title, children }: { title: string; children: ReactNode }) {
  return (
    <section>
      <h2>{title}</h2>
      {children}
    </section>
  )
}

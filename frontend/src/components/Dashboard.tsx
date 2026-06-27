import { getAggregateStatus } from '../api'
import { useAsync } from '../useAsync'
import { ErrorBanner, Loading, Section, Status } from './ui'

export function Dashboard() {
  const { state, refetch } = useAsync(getAggregateStatus, [])
  return (
    <Section title="Dashboard">
      <button onClick={refetch}>Refresh</button>
      {state.status === 'loading' && <Loading />}
      {state.status === 'error' && <ErrorBanner message={state.error} />}
      {state.status === 'success' && (
        <div className="dashboard-grid">
          <div className="stat">
            <span className="stat-label">Running reviewers</span>
            <span className="stat-value">{state.data.running_reviewers}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Running implementors</span>
            <span className="stat-value">{state.data.running_implementors}</span>
          </div>
          <div className="stat">
            <span className="stat-label">Backlog size</span>
            <span className="stat-value">{state.data.backlog_size}</span>
          </div>
          <div className="failures">
            <h3>Recent failures</h3>
            {state.data.recent_failures.length === 0 ? (
              <p className="empty">No recent failures.</p>
            ) : (
              <ul>
                {state.data.recent_failures.map((f) => (
                  <li key={`${f.kind}-${f.id}`}>
                    <Status kind={f.kind}>{f.kind}</Status> #{f.id} (project {f.project_id})
                    {f.error ? ` — ${f.error}` : ''}
                  </li>
                ))}
              </ul>
            )}
          </div>
        </div>
      )}
    </Section>
  )
}

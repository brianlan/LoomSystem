// NFR-8 / NFR-9 / NG-7 / NG-8 / NG-13: operator-facing non-goal notices.
export function Notices() {
  return (
    <div className="notices">
      <p>
        <strong>Trusted LAN only:</strong> Anyone on this network can access this
        instance and its credentials. There is no app-level authentication (NFR-8).
      </p>
      <p>
        <strong>No resource caps:</strong> The system does not enforce CPU, memory,
        or container-count limits. The operator trusts the host (NFR-9).
      </p>
      <p>
        <strong>Desktop only:</strong> This UI is not mobile-responsive (NG-7).
        English-only (NG-8). The browser UI is the only interface — no external
        API/SDK (NG-13).
      </p>
    </div>
  )
}

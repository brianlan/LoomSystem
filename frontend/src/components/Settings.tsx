import { useState } from 'react'
import * as api from '../api'
import type { AgentDefinition, DockerImage, ModelEntry } from '../types'
import { useAsync } from '../useAsync'
import { ErrorBanner, Loading, Section } from './ui'

// --- Agent definitions (with prompt markdown editor) ---
function AgentDefinitions() {
  const { state, refetch } = useAsync(api.listAgentDefinitions, [])
  const [editing, setEditing] = useState<AgentDefinition | null>(null)
  const [name, setName] = useState('')
  const [githubIdentity, setGithubIdentity] = useState('')
  const [prompt, setPrompt] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  function startNew() {
    setEditing({ id: 0, name: '', prompt_markdown: '', github_identity: '', permissions: {} })
    setName('')
    setGithubIdentity('')
    setPrompt('')
  }
  function startEdit(a: AgentDefinition) {
    setEditing(a)
    setName(a.name)
    setGithubIdentity(a.github_identity)
    setPrompt(a.prompt_markdown)
  }

  async function save() {
    setSaving(true)
    setError('')
    if (!name || !githubIdentity) {
      setError('Name and GitHub identity are required')
      setSaving(false)
      return
    }
    try {
      if (editing?.id) {
        await api.updateAgentDefinition(editing.id, {
          name,
          prompt_markdown: prompt,
          github_identity: githubIdentity,
        })
      } else {
        await api.createAgentDefinition({
          name,
          prompt_markdown: prompt,
          github_identity: githubIdentity,
          permissions: {},
        })
      }
      setEditing(null)
      refetch()
    } catch (e) {
      setError(message(e))
    } finally {
      setSaving(false)
    }
  }

  async function remove(id: number) {
    setError('')
    try {
      await api.deleteAgentDefinition(id)
      refetch()
    } catch (e) {
      setError(message(e))
    }
  }

  return (
    <div className="registry">
      <h3>Agent definitions</h3>
      <button onClick={startNew}>New agent definition</button>
      {error && <ErrorBanner message={error} />}
      {state.status === 'loading' && <Loading />}
      {state.status === 'error' && <ErrorBanner message={state.error} />}
      {state.status === 'success' && (
        <>
          {state.data.length === 0 ? (
            <p className="empty">No agent definitions yet.</p>
          ) : (
            <ul>
              {state.data.map((a) => (
                <li key={a.id}>
                  <strong>{a.name}</strong> (gh: {a.github_identity})
                  <button onClick={() => startEdit(a)}>Edit</button>
                  <button onClick={() => remove(a.id)}>Delete</button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
      {editing && (
        <div className="modal">
          <h4>{editing.id ? 'Edit agent' : 'New agent'}</h4>
          <label>
            Name
            <input value={name} onChange={(e) => setName(e.target.value)} />
          </label>
          <label>
            GitHub identity
            <input
              value={githubIdentity}
              onChange={(e) => setGithubIdentity(e.target.value)}
            />
          </label>
          <label>
            Prompt markdown
            <textarea
              className="prompt-editor"
              rows={12}
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
            />
          </label>
          <button onClick={save} disabled={saving}>
            {saving ? 'Saving…' : 'Save'}
          </button>
          <button onClick={() => setEditing(null)}>Cancel</button>
        </div>
      )}
    </div>
  )
}

// --- Model entries ---
function ModelEntries() {
  const { state, refetch } = useAsync(api.listModelEntries, [])
  const [provider, setProvider] = useState('')
  const [model, setModel] = useState('')
  const [creds, setCreds] = useState('')
  const [display, setDisplay] = useState('')
  const [error, setError] = useState('')

  async function add() {
    setError('')
    if (!provider || !model || !creds) {
      setError('provider_id, model_id, and credentials are required')
      return
    }
    try {
      await api.createModelEntry({
        provider_id: provider,
        model_id: model,
        credentials: creds,
        display_name: display || null,
      })
      setProvider('')
      setModel('')
      setCreds('')
      setDisplay('')
      refetch()
    } catch (e) {
      setError(message(e))
    }
  }

  async function remove(id: number) {
    setError('')
    try {
      await api.deleteModelEntry(id)
      refetch()
    } catch (e) {
      setError(message(e))
    }
  }

  return (
    <div className="registry">
      <h3>Model entries</h3>
      {error && <ErrorBanner message={error} />}
      <div className="form-row">
        <input placeholder="provider_id" value={provider} onChange={(e) => setProvider(e.target.value)} />
        <input placeholder="model_id" value={model} onChange={(e) => setModel(e.target.value)} />
        <input placeholder="credentials" value={creds} onChange={(e) => setCreds(e.target.value)} />
        <input placeholder="display_name (optional)" value={display} onChange={(e) => setDisplay(e.target.value)} />
        <button onClick={add}>Add</button>
      </div>
      {state.status === 'loading' && <Loading />}
      {state.status === 'error' && <ErrorBanner message={state.error} />}
      {state.status === 'success' && (
        <>
          {state.data.length === 0 ? (
            <p className="empty">No model entries yet.</p>
          ) : (
            <ul>
              {state.data.map((m: ModelEntry) => (
                <li key={m.id}>
                  {m.provider_id}/{m.model_id}
                  {m.display_name ? ` (${m.display_name})` : ''}
                  <button onClick={() => remove(m.id)}>Delete</button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

// --- Docker images ---
function DockerImages() {
  const { state, refetch } = useAsync(api.listDockerImages, [])
  const [image, setImage] = useState('')
  const [error, setError] = useState('')

  async function add() {
    setError('')
    if (!image) return
    try {
      await api.createDockerImage({ image_name: image })
      setImage('')
      refetch()
    } catch (e) {
      setError(message(e))
    }
  }

  async function remove(id: number) {
    setError('')
    try {
      await api.deleteDockerImage(id)
      refetch()
    } catch (e) {
      setError(message(e))
    }
  }

  return (
    <div className="registry">
      <h3>Docker images</h3>
      {error && <ErrorBanner message={error} />}
      <div className="form-row">
        <input placeholder="image_name" value={image} onChange={(e) => setImage(e.target.value)} />
        <button onClick={add}>Add</button>
      </div>
      {state.status === 'loading' && <Loading />}
      {state.status === 'error' && <ErrorBanner message={state.error} />}
      {state.status === 'success' && (
        <>
          {state.data.length === 0 ? (
            <p className="empty">No docker images yet.</p>
          ) : (
            <ul>
              {state.data.map((d: DockerImage) => (
                <li key={d.id}>
                  {d.image_name}
                  <button onClick={() => remove(d.id)}>Delete</button>
                </li>
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  )
}

// --- SSH key ---
function SshKey() {
  const [value, setValue] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  async function save() {
    setError('')
    setStatus('')
    if (!value) {
      setError('SSH key is required')
      return
    }
    try {
      await api.setSshKey(value)
      setStatus('SSH key saved.')
      setValue('')
    } catch (e) {
      setError(message(e))
    }
  }

  async function check() {
    setError('')
    setStatus('')
    try {
      await api.getSshKey()
      setStatus('SSH key is set.')
    } catch (e) {
      setStatus('SSH key is not set.')
    }
  }

  async function clear() {
    setError('')
    setStatus('')
    try {
      await api.deleteSshKey()
      setStatus('SSH key cleared.')
    } catch (e) {
      setError(message(e))
    }
  }

  return (
    <div className="registry">
      <h3>SSH key</h3>
      {error && <ErrorBanner message={error} />}
      {status && <p className="status">{status}</p>}
      <textarea
        placeholder="-----BEGIN OPENSSH PRIVATE KEY-----"
        rows={4}
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <div className="form-row">
        <button onClick={save}>Save</button>
        <button onClick={check}>Check</button>
        <button onClick={clear}>Clear</button>
      </div>
    </div>
  )
}

// --- GitHub token ---
function GithubToken() {
  const [value, setValue] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  async function save() {
    setError('')
    setStatus('')
    if (!value) {
      setError('GitHub token is required')
      return
    }
    try {
      await api.setGithubToken(value)
      setStatus('GitHub token saved.')
      setValue('')
    } catch (e) {
      setError(message(e))
    }
  }

  async function check() {
    setError('')
    setStatus('')
    try {
      await api.getGithubToken()
      setStatus('GitHub token is set.')
    } catch {
      setStatus('GitHub token is not set.')
    }
  }

  async function clear() {
    setError('')
    setStatus('')
    try {
      await api.deleteGithubToken()
      setStatus('GitHub token cleared.')
    } catch (e) {
      setError(message(e))
    }
  }

  return (
    <div className="registry">
      <h3>App GitHub token</h3>
      {error && <ErrorBanner message={error} />}
      {status && <p className="status">{status}</p>}
      <input
        placeholder="ghp_…"
        value={value}
        onChange={(e) => setValue(e.target.value)}
      />
      <div className="form-row">
        <button onClick={save}>Save</button>
        <button onClick={check}>Check</button>
        <button onClick={clear}>Clear</button>
      </div>
    </div>
  )
}

// --- Triage config ---
function TriageConfigForm() {
  const { state, refetch } = useAsync(api.getTriageConfig, [])
  const [endpoint, setEndpoint] = useState('')
  const [model, setModel] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  // Sync loaded values into the form once.
  if (state.status === 'success' && !endpoint && state.data.endpoint_url) {
    setEndpoint(state.data.endpoint_url)
    setModel(state.data.model_name)
  }

  async function save() {
    setError('')
    setStatus('')
    if (!endpoint || !model || !apiKey) {
      setError('endpoint_url, model_name, and api_key are required')
      return
    }
    try {
      await api.setTriageConfig({ endpoint_url: endpoint, model_name: model, api_key: apiKey, headers: {} })
      setStatus('Triage config saved.')
      setApiKey('')
      refetch()
    } catch (e) {
      setError(message(e))
    }
  }

  async function clear() {
    setError('')
    setStatus('')
    try {
      await api.deleteTriageConfig()
      setEndpoint('')
      setModel('')
      setStatus('Triage config cleared.')
      refetch()
    } catch (e) {
      setError(message(e))
    }
  }

  return (
    <div className="registry">
      <h3>Triage config</h3>
      {error && <ErrorBanner message={error} />}
      {status && <p className="status">{status}</p>}
      {state.status === 'error' && <p className="status">Triage config not set.</p>}
      <input placeholder="endpoint_url" value={endpoint} onChange={(e) => setEndpoint(e.target.value)} />
      <input placeholder="model_name" value={model} onChange={(e) => setModel(e.target.value)} />
      <input placeholder="api_key" value={apiKey} onChange={(e) => setApiKey(e.target.value)} />
      <div className="form-row">
        <button onClick={save}>Save</button>
        <button onClick={clear}>Clear</button>
      </div>
    </div>
  )
}

// --- Proxy ---
function ProxyForm() {
  const { state, refetch } = useAsync(api.getProxy, [])
  const [http, setHttp] = useState('')
  const [https, setHttps] = useState('')
  const [status, setStatus] = useState('')
  const [error, setError] = useState('')

  if (state.status === 'success' && !http && state.data.http_proxy) {
    setHttp(state.data.http_proxy ?? '')
    setHttps(state.data.https_proxy ?? '')
  }

  async function save() {
    setError('')
    setStatus('')
    try {
      await api.setProxy({ http_proxy: http || null, https_proxy: https || null })
      setStatus('Proxy saved.')
      refetch()
    } catch (e) {
      setError(message(e))
    }
  }

  async function clear() {
    setError('')
    setStatus('')
    try {
      await api.deleteProxy()
      setHttp('')
      setHttps('')
      setStatus('Proxy cleared.')
      refetch()
    } catch (e) {
      setError(message(e))
    }
  }

  return (
    <div className="registry">
      <h3>Proxy</h3>
      {error && <ErrorBanner message={error} />}
      {status && <p className="status">{status}</p>}
      <input placeholder="http_proxy" value={http} onChange={(e) => setHttp(e.target.value)} />
      <input placeholder="https_proxy" value={https} onChange={(e) => setHttps(e.target.value)} />
      <div className="form-row">
        <button onClick={save}>Save</button>
        <button onClick={clear}>Clear</button>
      </div>
    </div>
  )
}

function message(e: unknown): string {
  return e instanceof Error ? e.message : String(e)
}

export function Settings() {
  return (
    <Section title="Settings">
      <AgentDefinitions />
      <ModelEntries />
      <DockerImages />
      <SshKey />
      <GithubToken />
      <TriageConfigForm />
      <ProxyForm />
    </Section>
  )
}

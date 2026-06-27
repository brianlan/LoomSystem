import { fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it } from 'vitest'

import { Settings } from './Settings'
import { errorResp, installFetch, json, restoreFetch, type MockRoute } from '../test/mockFetch'

function route(urlSuffix: string, body: unknown): MockRoute {
  return { match: (url) => url.endsWith(urlSuffix), respond: () => Promise.resolve(json(body)) }
}

const AGENTS: MockRoute = route('/settings/agent-definitions', [
  { id: 1, name: 'reviewer', prompt_markdown: '# hi', github_identity: 'bot', permissions: {} },
])
const MODELS: MockRoute = route('/settings/model-entries', [
  { id: 1, provider_id: 'anthropic', model_id: 'claude-3', display_name: null, custom_config: null },
])
const IMAGES: MockRoute = route('/settings/docker-images', [{ id: 1, image_name: 'img:latest' }])
const TRIAGE_NOT_SET: MockRoute = {
  match: (url) => url.endsWith('/settings/triage-config'),
  respond: () => Promise.resolve(errorResp(404, 'Triage config not set')),
}
const PROXY: MockRoute = route('/settings/proxy', { http_proxy: null, https_proxy: null })

const BASE_ROUTES = [AGENTS, MODELS, IMAGES, TRIAGE_NOT_SET, PROXY]

describe('Settings', () => {
  let original: typeof fetch
  beforeEach(() => {
    original = installFetch(BASE_ROUTES)
  })
  afterEach(() => restoreFetch(original))

  it('lists existing agent definitions, model entries, and docker images', async () => {
    render(<Settings />)
    await waitFor(() => expect(screen.getByText('reviewer')).toBeInTheDocument())
    expect(screen.getByText('anthropic/claude-3')).toBeInTheDocument()
    expect(screen.getByText('img:latest')).toBeInTheDocument()
  })

  it('shows empty states when registries are empty', async () => {
    original = installFetch([
      route('/settings/agent-definitions', []),
      route('/settings/model-entries', []),
      route('/settings/docker-images', []),
      TRIAGE_NOT_SET,
      PROXY,
    ])
    render(<Settings />)
    await waitFor(() => expect(screen.getByText(/no agent definitions yet/i)).toBeInTheDocument())
    expect(screen.getByText(/no model entries yet/i)).toBeInTheDocument()
    expect(screen.getByText(/no docker images yet/i)).toBeInTheDocument()
  })

  it('validates required fields before creating an agent definition', async () => {
    render(<Settings />)
    await waitFor(() => expect(screen.getByText('reviewer')).toBeInTheDocument())
    fireEvent.click(screen.getByText('New agent definition'))
    // The modal Save button is the last "Save" button in the document.
    const modal = screen.getByText('New agent').closest('div.modal') as HTMLElement
    fireEvent.click(within(modal).getByRole('button', { name: 'Save' }))
    await waitFor(() =>
      expect(screen.getByText(/name and github identity are required/i)).toBeInTheDocument(),
    )
  })

  it('surfaces an in-use deletion error for a docker image', async () => {
    const imagesList: MockRoute = route('/settings/docker-images', [
      { id: 2, image_name: 'busy:latest' },
    ])
    const deleteErr: MockRoute = {
      match: (url, init) =>
        url.endsWith('/settings/docker-images/2') && init?.method === 'DELETE',
      respond: () => Promise.resolve(errorResp(409, 'Docker image 2 is in use')),
    }
    original = installFetch([AGENTS, MODELS, imagesList, deleteErr, TRIAGE_NOT_SET, PROXY])
    render(<Settings />)
    await waitFor(() => expect(screen.getByText('busy:latest')).toBeInTheDocument())
    // Scope the delete click to the docker-image list item.
    const li = screen.getByText('busy:latest').closest('li') as HTMLElement
    fireEvent.click(within(li).getByText('Delete'))
    await waitFor(() => expect(screen.getByText(/in use/i)).toBeInTheDocument())
  })

  it('reports GitHub token status when not set', async () => {
    const tokenMissing: MockRoute = {
      match: (url, init) =>
        url.endsWith('/settings/github-token') && init?.method !== 'PUT',
      respond: () => Promise.resolve(errorResp(404, 'GitHub token not set')),
    }
    original = installFetch([...BASE_ROUTES, tokenMissing])
    render(<Settings />)
    await waitFor(() => expect(screen.getByText('reviewer')).toBeInTheDocument())
    // The GitHub token "Check" button lives in the App GitHub token section.
    const checkButtons = screen.getAllByRole('button', { name: 'Check' })
    fireEvent.click(checkButtons[checkButtons.length - 1])
    await waitFor(() => expect(screen.getByText(/github token is not set/i)).toBeInTheDocument())
  })
})

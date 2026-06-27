import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'

import { Notices } from './Notices'

describe('Notices', () => {
  it('renders the trusted-LAN / no-auth notice (NFR-8)', () => {
    render(<Notices />)
    expect(screen.getByText(/trusted lan only/i)).toBeInTheDocument()
    expect(screen.getByText(/Anyone on this network/i)).toBeInTheDocument()
  })

  it('renders the no-resource-caps notice (NFR-9)', () => {
    render(<Notices />)
    expect(screen.getByText(/no resource caps/i)).toBeInTheDocument()
  })

  it('renders the desktop-only and no-API notices (NG-7/NG-8/NG-13)', () => {
    render(<Notices />)
    expect(screen.getByText(/desktop only/i)).toBeInTheDocument()
    expect(screen.getByText(/no external\s+API\/SDK/i)).toBeInTheDocument()
  })
})

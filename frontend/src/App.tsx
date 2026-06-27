import { useState } from 'react'
import { Dashboard } from './components/Dashboard'
import { Notices } from './components/Notices'
import { Operator } from './components/Operator'
import { Projects } from './components/Projects'
import { Settings } from './components/Settings'
import './app.css'

type Tab = 'dashboard' | 'projects' | 'operator' | 'settings'

const TABS: { id: Tab; label: string }[] = [
  { id: 'dashboard', label: 'Dashboard' },
  { id: 'projects', label: 'Projects' },
  { id: 'operator', label: 'Operator' },
  { id: 'settings', label: 'Settings' },
]

function App() {
  const [tab, setTab] = useState<Tab>('dashboard')
  return (
    <div className="app">
      <header>
        <h1>LoomSystem</h1>
        <nav>
          {TABS.map((t) => (
            <button
              key={t.id}
              className={tab === t.id ? 'tab active' : 'tab'}
              onClick={() => setTab(t.id)}
            >
              {t.label}
            </button>
          ))}
        </nav>
      </header>
      <Notices />
      <main>
        {tab === 'dashboard' && <Dashboard />}
        {tab === 'projects' && <Projects />}
        {tab === 'operator' && <Operator />}
        {tab === 'settings' && <Settings />}
      </main>
    </div>
  )
}

export default App

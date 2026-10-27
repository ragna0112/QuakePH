import { useEffect, useState } from 'react'
import './App.css'

type Config = {
  coverage: string
  bounds: { min_latitude: number; max_latitude: number; min_longitude: number; max_longitude: number }
  display_timezone: string
}

export default function App() {
  const [config, setConfig] = useState<Config | null>(null)
  const [status, setStatus] = useState('Checking API…')
  const [checkedAt, setCheckedAt] = useState<string | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    async function check() {
      try {
        const [configuration, readiness] = await Promise.all([
          fetch('/api/config', { signal: controller.signal }),
          fetch('/health/ready', { signal: controller.signal }),
        ])
        if (!configuration.ok) throw new Error('API configuration unavailable')
        setConfig(await configuration.json())
        setStatus(readiness.ok ? 'API and database ready' : 'API reachable · database unavailable')
        setCheckedAt(new Date().toISOString())
      } catch {
        if (!controller.signal.aborted) setStatus('API unavailable · start the backend and refresh')
      }
    }
    void check()
    return () => controller.abort()
  }, [])
  return (
    <main>
      <header><span className="eyebrow">EARTHQUAKE DATA PROJECT</span><h1>QuakePH<span>°</span></h1>
        <p>Philippines and surrounding region</p></header>
      <section aria-labelledby="foundation"><span className="badge">Milestone 1</span>
        <h2 id="foundation">A foundation for understanding our region.</h2>
        <p>The application shell is ready. Earthquake ingestion, the interactive map, and the event table will arrive in later milestones.</p>
        <p className="status" role="status">{status}</p>
        {checkedAt && <small>Checked: {new Intl.DateTimeFormat('en-PH', {
          dateStyle: 'medium', timeStyle: 'medium', timeZone: 'Asia/Manila',
        }).format(new Date(checkedAt))} · Asia/Manila (UTC+08:00)</small>}
      </section>
      <section aria-labelledby="coverage"><h2 id="coverage">Coverage</h2>
        {config ? <p>{config.bounds.min_latitude}° to {config.bounds.max_latitude}° latitude · {config.bounds.min_longitude}° to {config.bounds.max_longitude}° longitude</p>
          : <p>Coverage configuration loads from the backend.</p>}
        <p>This bounding box includes nearby waters. It is not an exact national boundary.</p>
      </section>
      <footer>No earthquake records have been imported. Future data source: USGS.<br />Display timezone: Asia/Manila (UTC+08:00) · Storage timezone: UTC</footer>
    </main>
  )
}

import { useState } from 'react'
import type { EventList, MapData, PipelineStatus, RunList, Statistics } from './apiTypes'
import { useResource, useViewState } from './data'
import { appliedView, defaultView, encodeView, requests } from './filters'
import { day, mag, time } from './presentation'
import FilterForm from './components/FilterForm'
import MapPanel from './components/MapPanel'
import Events from './components/Events'
import Activity from './components/Activity'
import Pipeline from './components/Pipeline'
import Details from './components/Details'
import { LoadState } from './components/Shared'
import './App.css'

export default function App() {
  const {view,notice,navigate}=useViewState()
  const [refresh,setRefresh]=useState(0), [selected,setSelected]=useState<string|null>(null), [runPage,setRunPage]=useState(1)
  const urls=requests(view)
  const events=useResource<EventList>(urls.list,refresh)
  const map=useResource<MapData>(urls.map,refresh)
  const stats=useResource<Statistics>(urls.stats,refresh)
  const status=useResource<PipelineStatus>('/api/pipeline/status',refresh)
  const runs=useResource<RunList>(`/api/pipeline/runs?page=${runPage}&page_size=5`,refresh)
  const lastCollection=status.data?.tasks.find(task=>task.name==='recent')?.last_success_at
  const data=stats.data
  function change(next:typeof view) {setSelected(null);navigate(next)}
  return <><a className="skip-link" href="#dashboard-content">Skip to dashboard</a>
    <header className="site-header"><div className="brand"><svg viewBox="0 0 32 32" width="34" height="34" aria-hidden="true"><rect x="1" y="1" width="30" height="30" rx="9" fill="none" stroke="currentColor"/><path d="M5 17h5l3-8 5 16 3-8h6" fill="none" stroke="currentColor" strokeWidth="2"/></svg><div><h1>QuakePH</h1><p>Philippines and surrounding region</p></div></div>
      <div className="header-actions"><div className="freshness"><span>Successful collection</span><strong>{status.data?time(lastCollection):status.loading?'Checking stored status…':'Status unavailable'}</strong><small>{status.error&&status.data?'Cached status · ':''}{status.fetchedAt?`Browser fetched ${time(status.fetchedAt)}`:'No successful browser fetch yet'}</small></div><button className="refresh" onClick={()=>setRefresh(value=>value+1)}><span aria-hidden="true">↻</span> Refresh view</button></div>
    </header>
    <div className="workspace"><aside><FilterForm key={JSON.stringify(view.filters)} filters={view.filters} onApply={filters=>change(appliedView(view,filters))} onReset={()=>change(defaultView())}/><p className="coverage-note">Coverage is a configurable rectangle including nearby waters, not an exact national boundary.</p></aside>
      <main id="dashboard-content" tabIndex={-1}>
        <div className="view-heading"><div><span className="eyebrow">Stored earthquake observations</span><h2>{day(view.filters.start)} – {day(view.filters.end)}</h2></div><span className="timezone">Asia/Manila · UTC+08:00</span></div>
        {notice&&<p className="notice" role="status">{notice}</p>}
        <LoadState resource={stats} name="Statistics"/>
        <section className="summary-grid" aria-busy={stats.loading} aria-label="Statistics for all matching records">
          <article className="stat-card"><span>Matching earthquakes</span><strong>{data?data.matching_count.toLocaleString():'—'}</strong><small>All matching stored records</small></article>
          <article className="stat-card"><span>Strongest matching</span><strong>{data?.strongest?mag(data.strongest.magnitude):data?'None recorded':'—'}</strong><small>{data?.strongest?.place || (data?.strongest?'Place not reported':'Excludes unknown magnitudes')}</small></article>
          <article className="stat-card"><span>Latest matching</span><strong className="latest-value">{data?.latest?time(data.latest.event_time):data?'None recorded':'—'}</strong><small>{data?.latest?.place || (data?.latest?'Place not reported':'Event time, not collection time')}</small></article>
        </section>
        <p className="snapshot-note">Panels are fetched independently; counts can differ during imports. Refresh view reloads stored data only.</p>
        <MapPanel resource={map} onSelect={setSelected}/>
        <div className="bottom-grid"><Events resource={events} onSelect={setSelected} sort={view.sort} onSort={sort=>change({...view,sort,page:1})} page={view.page} pageSize={view.pageSize} onPage={page=>change({...view,page})} onPageSize={pageSize=>change({...view,pageSize,page:1})}/><Activity resource={stats}/></div>
        <Pipeline status={status} runs={runs} runPage={runPage} onRunPage={setRunPage}/>
        <footer>Earthquake data: <a href="https://earthquake.usgs.gov/" target="_blank" rel="noopener noreferrer">USGS</a>. Educational project; not an official earthquake alert service.<br/><span>Display: Asia/Manila (UTC+08:00) · Storage: UTC · </span><a href={`?${encodeView(view)}`}>Link to this view</a></footer>
      </main>
    </div>
    {selected&&<Details key={selected} id={selected} onClose={()=>setSelected(null)}/>}
  </>
}

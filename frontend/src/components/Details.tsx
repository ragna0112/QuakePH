import { useEffect, useRef, useState } from 'react'
import type { EventDetail } from '../apiTypes'
import { useResource } from '../data'
import { time, revisionValue } from '../presentation'
import { FetchTime, LoadState, Magnitude } from './Shared'
export default function Details({id,onClose}:{id:string;onClose:()=>void}) {
  const dialog=useRef<HTMLDialogElement>(null)
  const [refresh,setRefresh]=useState(0)
  const resource=useResource<EventDetail>(`/api/earthquakes/${encodeURIComponent(id)}`,refresh)
  const data=resource.data
  useEffect(()=>{
    const element=dialog.current!
    const opener=document.activeElement instanceof HTMLElement?document.activeElement:null
    element.showModal()
    return ()=>{element.close();if(opener?.isConnected)opener.focus();else document.getElementById('events-heading')?.focus()}
  },[])
  return <dialog ref={dialog} className="detail-dialog" aria-labelledby="detail-heading" onKeyDown={event=>{
    if(event.key!=='Tab')return
    const controls=[...event.currentTarget.querySelectorAll<HTMLElement>('button:not(:disabled), a[href], input, select, [tabindex="0"]')]
    const first=controls[0],last=controls.at(-1)
    if(event.shiftKey&&document.activeElement===first){event.preventDefault();last?.focus()}
    else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first?.focus()}
  }} onCancel={event=>{event.preventDefault();onClose()}} onClick={event=>{if(event.target===event.currentTarget)onClose()}}>
    <div className="detail-content"><header className="detail-header"><div><p className="eyebrow">Stored event · {id}</p><h2 id="detail-heading">{data?.place || (data?'Place not reported':'Earthquake details')}</h2></div><button className="close-dialog" aria-label="Close event details" onClick={onClose}>×</button></header>
      <LoadState resource={resource} name="Event details"/>
      {resource.error&&<button onClick={()=>setRefresh(value=>value+1)}>Retry details</button>}
      {data&&<><div className="detail-overview"><Magnitude value={data.magnitude}/><span>{data.depth_km.toLocaleString()} km depth</span><span>{data.magnitude_type || 'Magnitude type not reported'}</span></div>
        <dl className="detail-facts"><div><dt>Event time</dt><dd>{time(data.event_time)}</dd></div><div><dt>USGS updated</dt><dd>{time(data.source_updated_at)}</dd></div><div><dt>First collected</dt><dd>{time(data.first_seen_at)}</dd></div><div><dt>Last seen</dt><dd>{time(data.last_seen_at)}</dd></div><div><dt>Coordinates</dt><dd>{data.latitude.toFixed(3)}° lat, {data.longitude.toFixed(3)}° lon</dd></div><div><dt>Coverage</dt><dd>{data.coverage_status==='inside'?'Inside configured region':'Outside configured region'}</dd></div><div><dt>Source state / review</dt><dd>{data.source_state} · {data.review_status || 'Review status unknown'}</dd></div><div><dt>Latest source check</dt><dd>{data.last_source_observation || 'No observation recorded'}<br/>{time(data.source_checked_at)}</dd></div></dl>
        <p className="muted fine-print">Times: Asia/Manila (UTC+08:00). {data.source_status_note}</p>
        {data.source_url?<a className="source-link" href={data.source_url} target="_blank" rel="noopener noreferrer">View USGS event page ↗</a>:<p className="muted">Source URL not recorded.</p>}
        <section className="revisions"><h3>Accepted revision history</h3><p className="muted">{data.revisions.returned} of {data.revisions.total} recorded revisions</p>
          {data.revisions.truncated&&<p className="notice">Recent history is limited to {data.revisions.limit} revisions; older revisions are not shown.</p>}
          {data.revisions.items.length===0?<p>No newer source revisions recorded for this event.</p>:data.revisions.items.map(revision=><article key={revision.id} className="revision"><h4>USGS version {time(revision.new_updated_at)}</h4><p className="muted">Previous version: {time(revision.previous_updated_at)}<br/>Recorded: {time(revision.recorded_at)}</p>
            {Object.keys(revision.changes).length===0?<p>Source timestamp advanced without changes to tracked fields.</p>:<dl>{Object.entries(revision.changes).map(([field,change])=><div key={field}><dt>{field.replaceAll('_',' ')}</dt><dd>{revisionValue(field,change.old)} → {revisionValue(field,change.new)}</dd></div>)}</dl>}
          </article>)}
        </section>
      </>}
      <FetchTime value={resource.fetchedAt}/>
    </div>
  </dialog>
}

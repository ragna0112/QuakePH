import type { EventList, Sort } from '../apiTypes'
import type { Resource } from '../data'
import { sorts } from '../filters'
import { time } from '../presentation'
import { FetchTime, LoadState, Magnitude } from './Shared'
export default function Events({resource,onSelect,sort,onSort,onPage,page,pageSize,onPageSize}:{resource:Resource<EventList>;onSelect:(id:string)=>void;sort:Sort;onSort:(sort:Sort)=>void;onPage:(page:number)=>void;page:number;pageSize:number;onPageSize:(size:number)=>void}) {
  const data=resource.data
  return <section className="panel events" aria-busy={resource.loading} aria-labelledby="events-heading">
    <div className="section-heading"><div><h2 id="events-heading" tabIndex={-1}>Earthquake records</h2><p>{data?`${data.total.toLocaleString()} matching records`:'Selected date range'}</p></div>
      <label className="sort-control">Sort<select value={sort} onChange={event=>onSort(event.target.value as Sort)}>{sorts.map(item=><option key={item.value} value={item.value}>{item.label}</option>)}</select></label>
    </div>
    <LoadState resource={resource} name="Records"/>
    {data&&data.items.length>0&&<div className="table-wrap"><table className="event-table"><thead><tr><th>Magnitude</th><th>Place / event time</th><th>Depth</th></tr></thead><tbody>{data.items.map(event=><tr key={event.usgs_id}>
      <td><Magnitude value={event.magnitude}/></td><td><button className="event-link" onClick={()=>onSelect(event.usgs_id)}>{event.place || 'Place not reported'}</button><time dateTime={event.event_time}>{time(event.event_time)}</time></td><td className="depth">{event.depth_km.toLocaleString()} <small>km</small></td>
    </tr>)}</tbody></table></div>}
    {data&&data.items.length===0&&<div className="empty"><strong>{data.total===0?'No stored events match these filters.':'No records on this page.'}</strong><p>{data.total===0?'The selected dates have not been changed. Try another range or review collection status below.':'Return to the first page to browse matching records.'}</p>{page>1&&<button onClick={()=>onPage(1)}>First page</button>}</div>}
    <div className="pagination"><label>Rows<select value={pageSize} onChange={e=>onPageSize(Number(e.target.value))}>{[...new Set([10,25,50,100,pageSize])].sort((a,b)=>a-b).map(size=><option key={size}>{size}</option>)}</select></label>
      <span>Page {page}{data?` of ${Math.max(1,data.pagination.total_pages)}`:''}</span><div><button aria-label="Previous page" disabled={page<=1||resource.loading} onClick={()=>onPage(page-1)}>←</button><button aria-label="Next page" disabled={!data?.pagination.has_next||resource.loading} onClick={()=>onPage(page+1)}>→</button></div>
    </div><FetchTime value={resource.fetchedAt}/>
  </section>
}

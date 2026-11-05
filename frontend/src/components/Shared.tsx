import type { Resource } from '../data'
import { mag, magClass, time } from '../presentation'
export function Magnitude({value}:{value:number|null}) {return <span className={`magnitude ${magClass(value)}`}>{mag(value)}</span>}
export function LoadState({resource,name}:{resource:Resource<unknown>;name:string}) {
  if(resource.error) return <p className="notice error" role="status">{name}: {resource.error} {resource.data ? `Showing cached results for these filters, fetched ${time(resource.fetchedAt)}.` : 'No results are shown for these filters. Use Refresh view to retry.'}</p>
  if(resource.loading) return <p className="loading" role="status">{resource.data?'Refreshing cached':'Loading'} {name.toLowerCase()}…</p>
  return null
}
export function FetchTime({value}:{value?:string}) {return value?<small className="fetch-time">Browser fetched {time(value)}</small>:null}

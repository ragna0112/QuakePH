import type { Sort } from './apiTypes'
export interface Filters { start: string; end: string; min_magnitude: string; max_magnitude: string; min_depth: string; max_depth: string; place: string }
export interface ViewState { filters: Filters; sort: Sort; page: number; pageSize: number }
export const sorts: {value: Sort; label: string}[] = [
  {value:'time_desc',label:'Newest first'}, {value:'time_asc',label:'Oldest first'},
  {value:'magnitude_desc',label:'Largest magnitude'}, {value:'magnitude_asc',label:'Smallest magnitude'},
  {value:'depth_asc',label:'Shallowest first'}, {value:'depth_desc',label:'Deepest first'},
  {value:'updated_desc',label:'Recently revised'},
]
const dayFormatter = new Intl.DateTimeFormat('en-CA', {timeZone:'Asia/Manila', year:'numeric', month:'2-digit', day:'2-digit'})
export function manilaDay(now = new Date()) {
  const parts = Object.fromEntries(dayFormatter.formatToParts(now).map(p => [p.type,p.value]))
  return `${parts.year}-${parts.month}-${parts.day}`
}
export function validDay(day: string) {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || +day.slice(0,4) < 1900 || +day.slice(0,4) > 9998) return false
  const date = new Date(`${day}T00:00:00Z`)
  return !Number.isNaN(date.getTime()) && date.toISOString().slice(0,10) === day
}
export function shiftDay(day: string, count: number) {
  return new Date(Date.parse(`${day}T00:00:00Z`) + count*86400000).toISOString().slice(0,10)
}
// Resolve Manila wall-clock midnight with Intl, rather than the browser's local
// timezone or a hardcoded UTC offset (historical Manila offsets also work).
export function manilaMidnight(day: string) {
  if (!validDay(day)) throw new Error('Choose a valid calendar date from 1900 through 9998.')
  const target = Date.parse(`${day}T00:00:00Z`)
  let instant = target
  const formatter = new Intl.DateTimeFormat('en-GB', {timeZone:'Asia/Manila', year:'numeric', month:'2-digit', day:'2-digit', hour:'2-digit', minute:'2-digit', second:'2-digit', hourCycle:'h23'})
  for (let i=0;i<4;i++) {
    const p = Object.fromEntries(formatter.formatToParts(new Date(instant)).map(part=>[part.type,part.value]))
    const wall = Date.parse(`${p.year}-${p.month}-${p.day}T${p.hour}:${p.minute}:${p.second}Z`)
    if (wall === target) return new Date(instant).toISOString()
    instant -= wall-target
  }
  throw new Error('Midnight is not representable for this Philippine calendar date.')
}
export function defaultView(now = new Date()): ViewState {
  const end = manilaDay(now)
  return {filters:{start:shiftDay(end,-6),end,min_magnitude:'',max_magnitude:'',min_depth:'',max_depth:'',place:''},sort:'time_desc',page:1,pageSize:10}
}
export function validateFilters(f: Filters): string | null {
  if (!validDay(f.start) || !validDay(f.end)) return 'Choose valid start and end dates (1900–9998).'
  if (f.start > f.end) return 'Start date must be on or before the inclusive end date.'
  for (const [lower,upper,name] of [[f.min_magnitude,f.max_magnitude,'Magnitude'],[f.min_depth,f.max_depth,'Depth']]) {
    if ([lower,upper].some(v=>v.trim() !== '' && (!/^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?$/i.test(v.trim()) || !Number.isFinite(Number(v))))) return `${name} must be a finite number.`
    if (lower.trim() !== '' && upper.trim() !== '' && Number(lower)>Number(upper)) return `${name} minimum must not exceed its maximum.`
  }
  if (f.place.trim().length > 200 || f.place.includes('\0')) return 'Place search must contain at most 200 characters and no NUL characters.'
  try { manilaMidnight(f.start); manilaMidnight(shiftDay(f.end,1)) } catch (error) { return (error as Error).message }
  return null
}
export function appliedView(current: ViewState, filters: Filters): ViewState {
  return {...current, filters:{...filters, place:filters.place.trim()},page:1}
}
export function encodeView(view: ViewState) {
  const params = new URLSearchParams()
  for (const [key,value] of Object.entries(view.filters)) if(value !== '') params.set(key,value)
  params.set('sort',view.sort)
  if(view.page!==1) params.set('page',String(view.page))
  if(view.pageSize!==10) params.set('page_size',String(view.pageSize))
  return params.toString()
}
export function restoreView(search: string, now = new Date()): {view: ViewState; notice: string | null} {
  const defaults = defaultView(now), view = {...defaults, filters:{...defaults.filters}}
  const params = new URLSearchParams(search)
  let malformed = false
  const allowed = new Set([...Object.keys(view.filters),'sort','page','page_size'])
  for(const key of params.keys()) if(!allowed.has(key)||params.getAll(key).length!==1) malformed=true
  for(const key of Object.keys(view.filters) as (keyof Filters)[]) if(params.has(key)) view.filters[key]=params.get(key)!
  if(validateFilters(view.filters)) {view.filters=defaults.filters;malformed=true}
  const sort = params.get('sort')
  if(sort && sorts.some(item=>item.value===sort)) view.sort=sort as Sort
  else if(sort) malformed=true
  for(const [urlKey,key,max] of [['page','page',100000],['page_size','pageSize',100]] as const) {
    if(params.has(urlKey)) {
      const value=params.get(urlKey)!
      if(/^\d+$/.test(value)&&+value>=1&&+value<=max) view[key]=+value
      else malformed=true
    }
  }
  return {view,notice:malformed?'The shared link contained invalid parameters. Review the displayed filters; defaults were substituted where necessary.':null}
}
export function apiFilters(filters: Filters) {
  const params = new URLSearchParams({start:manilaMidnight(filters.start),end:manilaMidnight(shiftDay(filters.end,1))})
  for(const key of ['min_magnitude','max_magnitude','min_depth','max_depth','place'] as const) if(filters[key].trim()!=='') params.set(key,filters[key].trim())
  return params
}
export function requests(view: ViewState) {
  const base=apiFilters(view.filters)
  const list=new URLSearchParams(base); list.set('page',String(view.page));list.set('page_size',String(view.pageSize));list.set('sort',view.sort)
  const map=new URLSearchParams(base);map.set('limit','1000')
  const stats=new URLSearchParams(base)
  stats.set('daily_days',String(Math.min(366, Math.round((Date.parse(`${view.filters.end}T00:00:00Z`)-Date.parse(`${view.filters.start}T00:00:00Z`))/86400000)+1)))
  return {list:`/api/earthquakes?${list}`,map:`/api/earthquakes/map?${map}`,stats:`/api/stats?${stats}`}
}

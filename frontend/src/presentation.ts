import type { MapData, PipelineStatus } from './apiTypes'
export function time(value: string | null | undefined) {
  return value ? `${new Intl.DateTimeFormat('en-PH',{timeZone:'Asia/Manila',dateStyle:'medium',timeStyle:'short'}).format(new Date(value))} PHT` : 'Not recorded'
}
export function day(value: string) {
  return new Intl.DateTimeFormat('en-PH',{timeZone:'Asia/Manila',month:'short',day:'numeric',year:'numeric'}).format(new Date(`${value}T00:00:00+08:00`))
}
export function mag(value: number | null) { return value===null?'M unknown':`M ${value.toFixed(1)}` }
export function magClass(value: number | null) {return value===null?'unknown':value<3?'low':value<5?'moderate':'higher'}
export function magColor(value: number | null) {return value===null?'#b4bfd2':value<3?'#f1d574':value<5?'#f4aa64':'#f38181'}
export function mapCount(data: MapData) {
  const {returned,total,truncated}=data.metadata
  return `${returned.toLocaleString()} of ${total.toLocaleString()} matching records shown${truncated?' · Map limit reached; use the list for all matching events.':'.'}`
}
export function chartLimit(excluded: number) {return `${excluded.toLocaleString()} matching events fall outside this chart’s date range. Summary statistics still include them.`}
export function workerLabel(state: PipelineStatus['worker_state']) {
  return {alive:'Fresh worker heartbeat',stale:'Worker heartbeat stale',stopped:'Worker stopped',not_observed:'Worker never observed'}[state]
}
export function revisionValue(field: string, value: unknown) {
  if(value===null||value===undefined)return 'Unknown'
  if(field==='event_time'&&typeof value==='string'&&Number.isFinite(Date.parse(value)))return time(value)
  if(typeof value==='number')return `${value.toLocaleString()}${field==='depth_km'?' km':field==='longitude'||field==='latitude'?'°':''}`
  return typeof value==='object'?JSON.stringify(value):String(value)
}

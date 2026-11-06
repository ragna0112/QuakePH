import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { expect,it,vi } from 'vitest'
import App from '../App'
import Activity from '../components/Activity'
import Pipeline from '../components/Pipeline'
import { listed,mapped,stats,status,runs,detail } from './fixtures'
import { mapCount } from '../presentation'
vi.mock('../components/MapPanel',()=>({default:({resource}:{resource:{data?:typeof mapped}})=><div>{resource.data?mapCount(resource.data):'Loading selected map filters'}</div>}))
function serve(path:string) {
  const url=new URL(path,'http://localhost')
  if(url.pathname.endsWith('/map'))return mapped
  if(url.pathname==='/api/earthquakes/fixture')return detail
  if(url.pathname==='/api/earthquakes')return {...listed,pagination:{...listed.pagination,page:Number(url.searchParams.get('page')),has_next:Number(url.searchParams.get('page'))<3}}
  if(url.pathname==='/api/stats')return stats
  if(url.pathname==='/api/pipeline/status')return status
  return runs
}
function ok(value:unknown){return new Response(JSON.stringify(value),{status:200})}
it('sorts and applies filters through URL state while resetting pagination',async()=>{
  const fetcher=vi.fn((path:string)=>Promise.resolve(ok(serve(path))))
  vi.stubGlobal('fetch',fetcher)
  window.history.replaceState(null,'','/?start=2026-10-30&end=2026-11-05&page=3')
  const user=userEvent.setup();render(<App/>)
  await screen.findByRole('button',{name:'Test trench'})
  await user.selectOptions(screen.getByLabelText('Sort'),'magnitude_desc')
  await waitFor(()=>expect(new URLSearchParams(window.location.search).get('sort')).toBe('magnitude_desc'))
  expect(new URLSearchParams(window.location.search).get('page')).toBeNull()
  fireEvent.input(screen.getByLabelText('Start date'),{target:{value:'2026-10-04'}})
  await user.type(screen.getByLabelText('Place contains'),'trench')
  await user.click(screen.getByRole('button',{name:'Apply filters'}))
  await waitFor(()=>expect(new URLSearchParams(window.location.search).get('place')).toBe('trench'))
  expect(fetcher.mock.calls.some(([path])=>path.includes('place=trench')&&path.includes('page=1'))).toBe(true)
  expect(new URLSearchParams(window.location.search).get('start')).toBe('2026-10-04')
})
it('shows partial failures without blanking successful panels, and labels bounded charts',async()=>{
  vi.stubGlobal('fetch',vi.fn((path:string)=>Promise.resolve(path.startsWith('/api/stats')?new Response('',{status:503}):ok(serve(path)))))
  render(<App/>)
  await screen.findByRole('button',{name:'Test trench'})
  await screen.findByText(/Statistics: Request failed \(HTTP 503\)/)
  expect(screen.getAllByText('Worker stopped').length).toBeGreaterThan(0)
  expect(screen.queryByText('21 matching records')).not.toBeNull()
})
it('does not show old-filter records while a new-filter request is pending or failed',async()=>{
  let reject!:(error:Error)=>void
  vi.stubGlobal('fetch',vi.fn((path:string)=>{
    if(path.includes('place=unavailable'))return new Promise<Response>((_,fail)=>{reject=fail})
    return Promise.resolve(ok(serve(path)))
  }))
  const user=userEvent.setup();render(<App/>)
  await screen.findByRole('button',{name:'Test trench'})
  await user.type(screen.getByLabelText('Place contains'),'unavailable')
  await user.click(screen.getByRole('button',{name:'Apply filters'}))
  expect(screen.queryByRole('button',{name:'Test trench'})).toBeNull()
  // One independently failing request must not revive a different filter's cache.
  reject(new Error('Controlled failure'))
  await screen.findByText(/Statistics: Controlled failure/)
  expect(screen.queryByRole('button',{name:'Test trench'})).toBeNull()
})
it('opens event details and restores keyboard focus when closed',async()=>{
  vi.stubGlobal('fetch',vi.fn((path:string)=>Promise.resolve(ok(serve(path)))))
  const user=userEvent.setup();render(<App/>)
  const opener=await screen.findByRole('button',{name:'Test trench'})
  await user.click(opener)
  const modal=await screen.findByRole('dialog')
  await screen.findByRole('link',{name:/View USGS event page/})
  expect(modal.getAttribute('aria-labelledby')).toBe('detail-heading')
  fireEvent(modal,new Event('cancel',{bubbles:false,cancelable:true}))
  await waitFor(()=>expect(screen.queryByRole('dialog')).toBeNull())
  expect(document.activeElement).toBe(opener)
})
it('renders bounded chart and worker state semantics as text',()=>{
  render(<><Activity resource={{data:stats,loading:false}}/><Pipeline status={{data:{...status,worker_state:'alive',tasks:[{...status.tasks[0],recorded_running:true,recorded_status:'running'}]},loading:false}} runs={{data:runs,loading:false}} runPage={1} onRunPage={()=>{}}/></>)
  expect(screen.getByText(/20 matching events fall outside/)).toBeTruthy()
  expect(screen.getByText(/Zero stored events does not prove/)).toBeTruthy()
  expect(screen.getAllByText('Fresh worker heartbeat').length).toBeGreaterThan(0)
  expect(screen.getByText(/execution ownership is not confirmed/)).toBeTruthy()
  expect(screen.getByText('Daily counts table')).toBeTruthy()
})
it('shows one-shot collection success without claiming a live worker',async()=>{
  vi.stubGlobal('fetch',vi.fn((path:string)=>Promise.resolve(ok(path==='/api/pipeline/status'
    ?{...status,tasks:[],worker_state:'not_observed',workers:[],recent_success_at:'2026-11-05T12:08:00Z',recent_attempt_at:'2026-11-05T12:07:00Z'}
    :serve(path)))))
  render(<App/>)
  await screen.findByRole('button',{name:'Test trench'})
  await screen.findAllByText('Worker never observed')
  expect(screen.getAllByText('Nov 5, 2026, 8:08 PM PHT').length).toBeGreaterThan(0)
  expect(screen.queryByText('Fresh worker heartbeat')).toBeNull()
})

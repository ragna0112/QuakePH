import { act, renderHook, waitFor } from '@testing-library/react'
import { it,expect,vi } from 'vitest'
import { useResource,useViewState } from '../data'
import { defaultView,encodeView } from '../filters'
function response(value:unknown){return new Response(JSON.stringify(value),{status:200})}
it('aborts and ignores obsolete requests, even if the transport ignores abort',async()=>{
  let oldResolve!:(response:Response)=>void
  const fetcher=vi.fn().mockImplementationOnce(()=>new Promise<Response>(resolve=>{oldResolve=resolve})).mockResolvedValueOnce(response({value:'new'}))
  vi.stubGlobal('fetch',fetcher)
  const {result,rerender}=renderHook(({url})=>useResource<{value:string}>(url,0),{initialProps:{url:'/old'}})
  rerender({url:'/new'})
  expect(result.current.data).toBeUndefined()
  await waitFor(()=>expect(result.current.data?.value).toBe('new'))
  await act(async()=>oldResolve(response({value:'obsolete'})))
  expect(result.current.data?.value).toBe('new')
  expect(fetcher.mock.calls[0][1].signal.aborted).toBe(true)
})
it('keeps exact-filter cache on refresh failure but hides it for other filters',async()=>{
  const fetcher=vi.fn().mockResolvedValueOnce(response({value:'cached'})).mockRejectedValue(new Error('Offline'))
  vi.stubGlobal('fetch',fetcher)
  const {result,rerender}=renderHook(({url,refresh})=>useResource<{value:string}>(url,refresh),{initialProps:{url:'/same',refresh:0}})
  await waitFor(()=>expect(result.current.data?.value).toBe('cached'))
  rerender({url:'/same',refresh:1})
  await waitFor(()=>expect(result.current.error).toBe('Offline'))
  expect(result.current.data?.value).toBe('cached');expect(result.current.fetchedAt).toBeTruthy()
  rerender({url:'/different',refresh:1})
  expect(result.current.data).toBeUndefined()
  await waitFor(()=>expect(result.current.loading).toBe(false))
  expect(result.current.data).toBeUndefined()
})
it('isolates simultaneous panel failures',async()=>{
  vi.stubGlobal('fetch',vi.fn((url:string)=>url==='/bad'?Promise.resolve(new Response('',{status:503})):Promise.resolve(response({ok:true}))))
  const {result}=renderHook(()=>({bad:useResource('/bad',0),good:useResource<{ok:boolean}>('/good',0)}))
  await waitFor(()=>expect(result.current.bad.error).toContain('HTTP 503'))
  expect(result.current.good.data?.ok).toBe(true)
})
it('restores filters and pages on back/forward popstate, and writes shareable URLs',()=>{
  const first=defaultView(new Date('2026-11-05T00:00Z'));first.filters.place='first'
  const second={...first,filters:{...first.filters,place:'second'},page:3}
  window.history.replaceState(null,'','/?'+encodeView(first))
  const {result}=renderHook(useViewState)
  expect(result.current.view.filters.place).toBe('first')
  act(()=>result.current.navigate(second))
  expect(window.location.search).toContain('place=second');expect(result.current.view.page).toBe(3)
  act(()=>{window.history.replaceState(null,'','/?'+encodeView(first));window.dispatchEvent(new PopStateEvent('popstate'))})
  expect(result.current.view.filters.place).toBe('first');expect(result.current.view.page).toBe(1)
  act(()=>{window.history.replaceState(null,'','/?'+encodeView(second));window.dispatchEvent(new PopStateEvent('popstate'))})
  expect(result.current.view.page).toBe(3)
})

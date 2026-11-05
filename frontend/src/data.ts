import { useEffect, useRef, useState } from 'react'
import { encodeView, restoreView } from './filters'
import type { ViewState } from './filters'
export interface Resource<T> { data?: T; fetchedAt?: string; loading: boolean; error?: string }
const base = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/$/,'')
export async function fetchJson<T>(path: string, signal: AbortSignal): Promise<T> {
  const response = await fetch(base+path,{signal,headers:{Accept:'application/json'}})
  if(!response.ok) throw new Error(`Request failed (HTTP ${response.status}).`)
  return response.json() as Promise<T>
}
export function useResource<T>(url: string, refresh: number): Resource<T> {
  const cache = useRef(new Map<string,{data:T;fetchedAt:string}>())
  const [state,setState] = useState<Resource<T>&{key:string}>({key:'',loading:true})
  useEffect(()=>{
    let active=true
    const controller=new AbortController()
    setState({key:url, ...cache.current.get(url), loading:true})
    void fetchJson<T>(url,controller.signal).then(data=>{
      if(!active || controller.signal.aborted) return
      const result={data,fetchedAt:new Date().toISOString()}
      cache.current.delete(url);cache.current.set(url,result)
      if(cache.current.size>8) cache.current.delete(cache.current.keys().next().value!)
      setState({key:url,...result,loading:false})
    }).catch(error=>{
      if(active&&!controller.signal.aborted) setState({key:url,...cache.current.get(url),loading:false,error:error instanceof Error?error.message:'Request could not be completed.'})
    })
    return ()=>{active=false;controller.abort()}
  },[url,refresh])
  // Hide old-filter data synchronously, even before the next effect runs.
  return state.key===url ? state : {loading:true}
}
export function useViewState() {
  const [restored,setRestored]=useState(()=>restoreView(window.location.search))
  useEffect(()=>{
    const pop=()=>setRestored(restoreView(window.location.search))
    window.addEventListener('popstate',pop)
    return ()=>window.removeEventListener('popstate',pop)
  },[])
  useEffect(()=>{
    const search=`?${encodeView(restored.view)}`
    if(window.location.search!==search) window.history.replaceState(null,'',window.location.pathname+search)
  },[restored.view])
  function navigate(view: ViewState) {
    const search=`?${encodeView(view)}`
    if(window.location.search!==search) window.history.pushState(null,'',window.location.pathname+search)
    setRestored({view,notice:null})
  }
  return {...restored,navigate}
}

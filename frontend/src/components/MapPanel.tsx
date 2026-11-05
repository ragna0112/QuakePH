import { useLayoutEffect, useRef, useState } from 'react'
import L from 'leaflet'
import 'leaflet/dist/leaflet.css'
import type { EventDetail, MapData } from '../apiTypes'
import type { Resource } from '../data'
import { fetchJson } from '../data'
import { mag, magColor, mapCount, time } from '../presentation'
import { FetchTime, LoadState } from './Shared'
const center: L.LatLngExpression=[12.8,122.2]
export default function MapPanel({resource,onSelect}:{resource:Resource<MapData>;onSelect:(id:string)=>void}) {
  const container=useRef<HTMLDivElement>(null), map=useRef<L.Map|null>(null), layer=useRef<L.FeatureGroup|null>(null)
  const tiles=useRef<L.TileLayer|null>(null), select=useRef(onSelect)
  const [tileFailed,setTileFailed]=useState(false)
  useLayoutEffect(()=>{select.current=onSelect},[onSelect])
  useLayoutEffect(()=>{
    if(!container.current) return
    const instance=L.map(container.current,{center,zoom:5,minZoom:3,maxZoom:13,scrollWheelZoom:false,
      zoomAnimation:false,fadeAnimation:false,markerZoomAnimation:false})
    map.current=instance
    tiles.current=L.tileLayer(import.meta.env.VITE_TILE_URL || 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',{
      maxZoom:19,attribution:import.meta.env.VITE_TILE_ATTRIBUTION || '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
    }).on('tileerror',()=>setTileFailed(true)).addTo(instance)
    layer.current=L.featureGroup().addTo(instance)
    const observer=new ResizeObserver(()=>instance.invalidateSize({pan:false}))
    observer.observe(container.current)
    return ()=>{observer.disconnect();instance.remove();map.current=null;layer.current=null;tiles.current=null}
  },[])
  useLayoutEffect(()=>{
    const markers=layer.current
    if(!markers) return
    markers.clearLayers()
    const controllers:AbortController[]=[]
    for(const feature of resource.data?.features ?? []) {
      const p=feature.properties
      const marker=L.circleMarker([feature.geometry.coordinates[1],feature.geometry.coordinates[0]],{
        radius:p.magnitude===null?6:Math.min(15,4+Math.max(0,p.magnitude)*1.5),color:magColor(p.magnitude),
        fillColor:magColor(p.magnitude),fillOpacity:p.magnitude===null?0.15:0.8,weight:2,dashArray:p.magnitude===null?'3 3':undefined,
      }).addTo(markers)
      const content=document.createElement('div');content.className='quake-popup'
      const heading=document.createElement('strong');heading.textContent=`${mag(p.magnitude)} · ${p.place || 'Place not reported'}`
      const info=document.createElement('p');info.textContent=`${p.depth_km.toLocaleString()} km depth · ${time(p.event_time)}`
      const source=document.createElement('a');source.textContent='Loading source link…';source.target='_blank';source.rel='noopener noreferrer'
      const detail=document.createElement('button');detail.textContent='Open event details';detail.onclick=()=>select.current(feature.id)
      content.append(heading,info,source,detail)
      marker.bindPopup(content,{maxWidth:280,autoPan:true,autoPanPadding:[25,25]})
      let current:AbortController|undefined
      marker.on('popupopen',()=>{
        current?.abort();current=new AbortController();controllers.push(current)
        const controller=current
        void fetchJson<EventDetail>(`/api/earthquakes/${encodeURIComponent(feature.id)}`,controller.signal).then(event=>{
          if(controller.signal.aborted)return
          if(event.source_url){source.href=event.source_url;source.textContent='USGS event page ↗'}
          else{source.removeAttribute('href');source.textContent='Source URL not recorded'}
        }).catch(()=>{if(!controller.signal.aborted)source.textContent='Source link unavailable; try event details.'})
      })
      marker.on('popupclose',()=>current?.abort())
      const element=marker.getElement()
      if(element) {
        element.setAttribute('tabindex','0');element.setAttribute('role','button')
        element.setAttribute('aria-label',`${mag(p.magnitude)}, ${p.place || 'place not reported'}. Open earthquake popup.`)
        element.addEventListener('keydown',event=>{const key=event as KeyboardEvent;if(key.key==='Enter'||key.key===' '){key.preventDefault();key.stopPropagation();marker.openPopup()}})
      }
    }
    return ()=>{controllers.forEach(controller=>controller.abort());markers.clearLayers()}
  },[resource.data])
  return <section className="panel map-panel" aria-busy={resource.loading} aria-labelledby="map-heading">
    <div className="section-heading"><div><h2 id="map-heading">Earthquake map</h2><p>{resource.data?mapCount(resource.data):'Philippines and surrounding waters'}</p></div>
      <div className="map-actions"><button onClick={()=>map.current?.setView(center,5,{animate:false})}>Reset view</button>
        <button disabled={!resource.data?.features.length} onClick={()=>{const bounds=layer.current?.getBounds();if(bounds?.isValid())map.current?.fitBounds(bounds,{padding:[30,30],maxZoom:8,animate:false})}}>Fit results</button></div>
    </div>
    <LoadState resource={resource} name="Map"/>
    {tileFailed&&<p className="notice" role="status">Some map tiles could not load. Event markers and the list remain available. <button onClick={()=>{setTileFailed(false);tiles.current?.redraw()}}>Retry tiles</button></p>}
    <a className="map-skip" href="#events-heading">Skip map to event list</a>
    <div className="map" ref={container} role="region" aria-label="Interactive earthquake map. Use the event list for equivalent keyboard-accessible details."/>
    <div className="map-footer"><div className="legend" aria-label="Magnitude legend"><span>Magnitude</span><span><i className="legend-dot low"/> &lt;3</span><span><i className="legend-dot moderate"/> 3–&lt;5</span><span><i className="legend-dot higher"/> ≥5</span><span><i className="legend-dot unknown"/> Unknown</span></div><FetchTime value={resource.fetchedAt}/></div>
  </section>
}

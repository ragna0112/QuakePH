import { render, screen, fireEvent } from '@testing-library/react'
import { expect,it,vi } from 'vitest'
// Leaflet feature detection needs SVG support before its module initializes.
Object.defineProperty(SVGSVGElement.prototype,'createSVGRect',{configurable:true,value:()=>({})})
it('initializes preloaded markers, clears obsolete markers, and handles tile failure',async()=>{
  vi.stubGlobal('ResizeObserver',class {observe(){} disconnect(){}})
  const {default:MapPanel}=await import('../components/MapPanel')
  const {mapped}=await import('./fixtures')
  const {rerender,container}=render(<MapPanel resource={{data:mapped,loading:false}} onSelect={()=>{}}/>)
  expect(screen.getByRole('button',{name:/M 4.2, Test trench. Open earthquake popup/})).toBeTruthy()
  expect(screen.getByText(/1 of 21 matching records shown/)).toBeTruthy()
  const tile=container.querySelector('img.leaflet-tile')
  if(!tile)throw new Error('Expected visible-view tile elements')
  fireEvent.error(tile)
  expect(screen.getByText(/Some map tiles could not load/)).toBeTruthy()
  expect(screen.getByRole('button',{name:'Fit results'})).toBeTruthy()
  rerender(<MapPanel resource={{loading:true}} onSelect={()=>{}}/>)
  expect(screen.queryByRole('button',{name:/M 4.2, Test trench/})).toBeNull()
})

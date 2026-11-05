import { describe,it,expect } from 'vitest'
import { defaultView, manilaMidnight, manilaDay, apiFilters, requests, restoreView, encodeView, appliedView, validateFilters } from '../filters'
import { mapCount, chartLimit, workerLabel } from '../presentation'
import { mapped } from './fixtures'
describe('Philippine dates, independent of local timezone',()=>{
  it.each([['2026-11-01','2026-10-31T16:00:00.000Z'],['2027-01-01','2026-12-31T16:00:00.000Z'],['2024-03-01','2024-02-29T16:00:00.000Z']])('resolves %s', (day,expected)=>expect(manilaMidnight(day)).toBe(expected))
  it('defaults to seven Manila days, including today, at a UTC date boundary',()=>{
    expect(manilaDay(new Date('2026-12-31T16:30:00Z'))).toBe('2027-01-01')
    const view=defaultView(new Date('2026-12-31T16:30:00Z'))
    expect(view.filters.start).toBe('2026-12-26');expect(view.filters.end).toBe('2027-01-01')
    expect(apiFilters(view.filters).get('end')).toBe('2027-01-01T16:00:00.000Z')
  })
  it('applies identical time and numeric filters, without leaking pagination into map/stats',()=>{
    const view=defaultView(new Date('2026-11-05T00:00Z'));view.filters.min_depth='-2.5';view.filters.min_magnitude='-1';view.page=3
    const urls=requests(view)
    const values=Object.values(urls).map(url=>new URL(url,'http://test').searchParams)
    for(const p of values){expect(p.get('start')).toBe('2026-10-29T16:00:00.000Z');expect(p.get('end')).toBe('2026-11-05T16:00:00.000Z');expect(p.get('min_depth')).toBe('-2.5')}
    expect(values[0].get('page')).toBe('3');expect(values[1].has('page')).toBe(false);expect(values[2].get('daily_days')).toBe('7')
  })
  it('round-trips URL state and resets pagination on applied filters',()=>{
    const view=defaultView(new Date('2026-11-05T00:00Z'));view.page=3;view.sort='depth_asc';view.filters.place='Bay & trench'
    expect(restoreView('?'+encodeView(view)).view).toEqual(view)
    expect(appliedView(view,{...view.filters,place:'new'}).page).toBe(1)
  })
  it('recovers malformed links without using source results to choose dates',()=>{
    const restored=restoreView('?start=nonsense&min_depth=0x12&sort=DROP&page=-1',new Date('2026-11-05T00:00Z'))
    expect(restored.notice).not.toBeNull();expect(restored.view).toEqual(defaultView(new Date('2026-11-05T00:00Z')))
    const emptyRange=restoreView('?start=1999-01-01&end=1999-01-01')
    expect(emptyRange.view.filters.start).toBe('1999-01-01');expect(emptyRange.notice).toBeNull()
  })
  it.each([{start:'2026-02-30'}, {start:'2027-01-01',end:'2026-01-01'}, {min_magnitude:'NaN'},{max_depth:'Infinity'},{min_depth:'0x12'},{min_depth:'10',max_depth:'-2'}])('rejects invalid ranges %j',change=>{
    expect(validateFilters({...defaultView().filters,...change})).not.toBeNull()
  })
})
it('states the map and chart bounds explicitly',()=>{
  expect(mapCount(mapped)).toContain('1 of 21');expect(mapCount(mapped)).toContain('use the list')
  expect(chartLimit(20)).toContain('20 matching events fall outside')
})
it.each([['stopped','Worker stopped'],['stale','Worker heartbeat stale'],['not_observed','Worker never observed'],['alive','Fresh worker heartbeat']] as const)('interprets worker state %s without inferring ingestion success',(state,label)=>expect(workerLabel(state)).toBe(label))
it('formats source-time revisions in Philippine time and preserves units',async()=>{
  const {revisionValue}=await import('../presentation')
  expect(revisionValue('event_time','2026-11-01T16:00:00Z')).toContain('Nov 2, 2026')
  expect(revisionValue('event_time','2026-11-01T16:00:00Z')).toContain('PHT')
  expect(revisionValue('depth_km',-2)).toBe('-2 km')
  expect(revisionValue('magnitude',null)).toBe('Unknown')
})

import { useState } from 'react'
import type { Filters } from '../filters'
import { validateFilters } from '../filters'
export default function FilterForm({filters,onApply,onReset}:{filters:Filters;onApply:(filters:Filters)=>void;onReset:()=>void}) {
  const [draft,setDraft]=useState(filters), [error,setError]=useState<string|null>(null)
  const [open,setOpen]=useState(()=>window.matchMedia('(min-width: 850px)').matches)
  function update(key:keyof Filters,value:string) {setDraft(previous=>({...previous,[key]:value}));setError(null)}
  return <details className="panel filters" open={open} onToggle={event=>setOpen(event.currentTarget.open)}>
    <summary><span>Filter records</span><span className="disclosure" aria-hidden="true">⌄</span></summary>
    <form noValidate onSubmit={event=>{event.preventDefault();const invalid=validateFilters(draft);setError(invalid);if(!invalid)onApply(draft)}}>
      <p className="muted filter-help">Philippine calendar days<br /><span>Asia/Manila · UTC+08:00</span></p>
      <div className="filter-fields">
        <label>Start date<input type="date" min="1900-01-01" max="9998-12-31" value={draft.start} onInput={e=>update('start',e.currentTarget.value)} required /></label>
        <label>End date <span className="hint">inclusive</span><input type="date" min="1900-01-01" max="9998-12-31" value={draft.end} onInput={e=>update('end',e.currentTarget.value)} required /></label>
        <fieldset><legend>Magnitude</legend><div className="range-inputs">
          <label>Minimum<input type="number" inputMode="decimal" step="any" placeholder="Any" value={draft.min_magnitude} onChange={e=>update('min_magnitude',e.target.value)}/></label>
          <label>Maximum<input type="number" inputMode="decimal" step="any" placeholder="Any" value={draft.max_magnitude} onChange={e=>update('max_magnitude',e.target.value)}/></label>
        </div></fieldset>
        <fieldset><legend>Depth <span className="hint">km</span></legend><div className="range-inputs">
          <label>Minimum<input type="number" inputMode="decimal" step="any" placeholder="Any" value={draft.min_depth} onChange={e=>update('min_depth',e.target.value)}/></label>
          <label>Maximum<input type="number" inputMode="decimal" step="any" placeholder="Any" value={draft.max_depth} onChange={e=>update('max_depth',e.target.value)}/></label>
        </div></fieldset>
        <label className="place-filter">Place contains<input type="search" placeholder="e.g. Mindanao" maxLength={200} value={draft.place} onChange={e=>update('place',e.target.value)}/></label>
      </div>
      {error&&<p className="notice error" role="alert">{error}</p>}
      <div className="filter-actions"><button className="primary" type="submit">Apply filters</button><button type="button" onClick={onReset}>Reset</button></div>
      <p className="muted fine-print">Active earthquakes in the configured box. Missing source checks do not imply deletion. Empty ranges stay empty.</p>
    </form>
  </details>
}

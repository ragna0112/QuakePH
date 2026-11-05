import type { Statistics } from '../apiTypes'
import type { Resource } from '../data'
import { chartLimit, day } from '../presentation'
import { FetchTime, LoadState } from './Shared'
export default function Activity({resource}:{resource:Resource<Statistics>}) {
  const data=resource.data?.daily
  const max=Math.max(1,...(data?.days.map(value=>value.count)??[]))
  return <section className="panel activity" aria-busy={resource.loading} aria-labelledby="activity-heading"><div className="section-heading"><div><h2 id="activity-heading">Daily activity</h2><p>Stored events · Philippine calendar days</p></div></div>
    <LoadState resource={resource} name="Daily activity"/>
    {data&&<><p className="chart-range">{day(data.start_day)} – {day(data.end_day)}</p>
      <div className="bars" role="img" aria-label={`Daily stored-event counts from ${day(data.start_day)} to ${day(data.end_day)}. Exact values follow in the daily counts table.`}>
        {data.days.map(value=><div className="bar-track" key={value.day} title={`${day(value.day)}: ${value.count} stored events`}><div className="bar" style={{height:`${value.count/max*100}%`}}/></div>)}
      </div><div className="chart-axis"><span>{day(data.start_day)}</span><span>{max} max/day</span><span>{day(data.end_day)}</span></div>
      {data.truncated&&<p className="notice">{chartLimit(data.events_outside_chart)}</p>}
      <p className="muted fine-print">Asia/Manila (UTC+08:00). Zero stored events does not prove no earthquakes occurred or that the period was fully imported.</p>
      <details className="daily-values"><summary>Daily counts table</summary><div className="daily-table-scroll"><table><thead><tr><th>Philippine day</th><th>Stored events</th></tr></thead><tbody>{data.days.map(value=><tr key={value.day}><td>{day(value.day)}</td><td>{value.count}</td></tr>)}</tbody></table></div></details>
    </>}
    <FetchTime value={resource.fetchedAt}/>
  </section>
}

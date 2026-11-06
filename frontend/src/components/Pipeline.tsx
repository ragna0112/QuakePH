import type { PipelineStatus, RunList } from '../apiTypes'
import type { Resource } from '../data'
import { time, workerLabel } from '../presentation'
import { FetchTime, LoadState } from './Shared'
export default function Pipeline({status,runs,onRunPage,runPage}:{status:Resource<PipelineStatus>;runs:Resource<RunList>;onRunPage:(page:number)=>void;runPage:number}) {
  const data=status.data
  const recent=data?.tasks.find(task=>task.name==='recent')
  const reconciliation=data?.tasks.find(task=>task.name==='reconcile')
  return <details className="panel pipeline" aria-busy={status.loading||runs.loading} open><summary><span>Collection & pipeline</span><span className="pipeline-state">{data?workerLabel(data.worker_state):status.loading?'Checking stored status…':'Status unavailable'} <span aria-hidden="true">⌄</span></span></summary>
    <div className="pipeline-content"><LoadState resource={status} name="Pipeline status"/>
      {data&&<><div className="pipeline-facts"><div><span className="eyebrow">Worker observation</span><strong>{workerLabel(data.worker_state)}</strong><p>{data.workers[0]?`Heartbeat ${time(data.workers[0].heartbeat_at)} · ${data.workers[0].heartbeat_fresh?'fresh at API observation':'not fresh at API observation'}`:'No worker has reported a heartbeat.'}</p><p>Recorded activity: {data.workers[0]?.recorded_activity || 'Not observed'}</p></div>
        <div><span className="eyebrow">Recent collection</span><strong>{time(data.recent_success_at ?? recent?.last_success_at)}</strong><p>Last attempted: {time(data.recent_attempt_at ?? recent?.last_attempt_at)}</p><p>Worker task result: {recent?.recorded_status || 'Not observed'}</p></div>
        <div><span className="eyebrow">Next collection · scheduled</span><strong>{time(recent?.next_run_at)}</strong><p>{recent?.overdue?'Overdue; a running worker is required.':'A scheduled time, not guaranteed execution.'}</p><p>Browser refresh reads storage only.</p></div>
      </div>
      <div className="reconciliation-line"><strong>Reconciliation · {reconciliation?.recorded_status || 'Not observed'}</strong><span>Scope: {reconciliation?.reconciliation_scope?.start?`${time(reconciliation.reconciliation_scope.start)} → ${time(reconciliation.reconciliation_scope.exclusive_end)} (end exclusive)`:'Not recorded'}</span><span>Next scheduled: {time(reconciliation?.next_run_at)}{reconciliation?.overdue?' · overdue':''}</span></div>
      <p className="muted fine-print">A heartbeat does not prove successful ingestion. {data.tasks.some(task=>task.recorded_running)?'A task is recorded running; execution ownership is not confirmed. ':'Execution ownership is not verified by this view. '}Times: Asia/Manila (UTC+08:00). Collection pauses when the worker stops or the Mac sleeps.</p>
      {data.recent_failures.length>0&&<p className="notice">{data.recent_failures.length} recent recorded failed/partial attempt{data.recent_failures.length===1?'':'s'}{data.failures_truncated?' (limited view)':''}. {data.recent_failures[0].errors.items[0]?.message || 'Inspect attempts below.'}</p>}
      <FetchTime value={status.fetchedAt}/></>}
      <h3>Recent attempts</h3><LoadState resource={runs} name="Run history"/>
      {runs.data&&<><p className="muted fine-print">{runs.data.total} recorded attempts. Counters belong to individual attempts, not combined job totals.</p><div className="table-wrap"><table className="run-table"><thead><tr><th>Attempt / Philippine time</th><th>Result</th><th>Fetched</th><th>Inserted / updated / unchanged</th></tr></thead><tbody>{runs.data.items.map(run=><tr key={run.id}><td><details><summary>{run.run_type.replaceAll('_',' ')}<span className="run-time">{time(run.started_at)}</span></summary><div className="run-more"><p>ID: {run.id}</p><p>Rejected: {run.counters.rejected} · Request failures: {run.counters.failed_request_attempts}</p>{run.reconciliation_id&&<p>Reconciliation: {run.reconciliation_id}</p>}{run.import_job_id&&<p>Historical job: {run.import_job_id}</p>}{run.errors.items.map((error,index)=><p key={index}>{error.message}{error.http_status?` HTTP ${error.http_status}.`:''}</p>)}{run.errors.truncated&&<p>Additional error summaries omitted.</p>}{run.status==='running'&&<p>Recorded running; owner not confirmed.</p>}</div></details></td><td>{run.status}</td><td>{run.counters.fetched}</td><td>{run.counters.inserted} / {run.counters.updated} / {run.counters.unchanged}</td></tr>)}</tbody></table></div>
      <div className="pagination"><span>Attempt page {runPage} of {Math.max(1,runs.data.pagination.total_pages)}</span><div><button aria-label="Previous attempts" disabled={runPage<=1||runs.loading} onClick={()=>onRunPage(runPage-1)}>←</button><button aria-label="Next attempts" disabled={!runs.data.pagination.has_next||runs.loading} onClick={()=>onRunPage(runPage+1)}>→</button></div></div></>}
      <FetchTime value={runs.fetchedAt}/>
    </div>
  </details>
}

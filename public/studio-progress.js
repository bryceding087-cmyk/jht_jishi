// Presentation only: current is the number of completed tasks, never a worker index.
const count=value=>Number.isFinite(Number(value))?Math.max(0,Math.floor(Number(value))):0;
const escape=value=>String(value??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

export function selectProductionProgress(studio={}){
  const stage=studio.stage||'';
  if(stage.startsWith('assets')){
    return studio.extraction_progress||{title:stage.startsWith('assets-catalog-')?'正在统一角色、场景与道具':'正在拆解资产与镜头',status:'running'};
  }
  if(['prompt-optimization','canvas'].includes(stage))return studio.prompt_optimization_progress||null;
  if(stage==='render'||!stage)return studio.production_progress||null;
  return null;
}

export function formatProgressDuration(value){
  const seconds=count(value),minutes=Math.floor(seconds/60),hours=Math.floor(minutes/60);
  if(hours)return `${hours} 小时 ${minutes%60} 分`;
  if(minutes)return `${minutes} 分 ${seconds%60} 秒`;
  return `${seconds} 秒`;
}

export function productionProgressModel(progress,runStatus){
  if(!progress)return null;
  const total=count(progress.total),current=total?Math.min(total,count(progress.current)):count(progress.current);
  let status=progress.status||'running';
  if(status!=='completed'&&['paused','blocked','cancelled','canceled'].includes(runStatus))status=runStatus;
  const completed=status==='completed';
  const parallel=['running','queued','failed','concurrency'].some(key=>progress[key]!=null);
  const batched=['batch_current','batch_total','batch_size','running_batches'].some(key=>progress[key]!=null);
  const running=completed?0:count(progress.running),failed=completed?0:count(progress.failed);
  const queued=completed?0:progress.queued!=null?count(progress.queued):Math.max(0,total-current-running-failed);
  const labels={running:'进行中',completed:'已完成',blocked:'需要处理',paused:'已暂停',cancelled:'已取消',canceled:'已取消'};
  const batchTotal=count(progress.batch_total),batchCurrent=batchTotal?Math.min(batchTotal,count(progress.batch_current)):count(progress.batch_current);
  const runningBatches=completed?0:count(progress.running_batches),batchSize=count(progress.batch_size);
  const metrics=[{label:batched?'已完成节点':parallel?'已完成':'进度',value:total?`${current} / ${total}`:String(current)}];
  if(parallel){
    metrics.push({label:batched?'运行节点':'运行中',value:String(running)},{label:batched?'排队节点':'排队',value:String(queued)});
    if(failed)metrics.push({label:batched?'需处理节点':'需处理',value:String(failed)});
  }
  return {title:progress.title||'正在准备制作任务',status,statusLabel:labels[status]||'进行中',current,total,parallel,running,queued,failed,
    concurrency:count(progress.concurrency),metrics,batched,batchTotal,batchCurrent,batchSize,runningBatches,
    elapsed:progress.elapsed_seconds!=null?formatProgressDuration(progress.elapsed_seconds):'',repaired:count(progress.repaired)};
}

export function renderProductionProgress(progress,runStatus){
  const model=productionProgressModel(progress,runStatus);
  if(!model)return '';
  const detail=[model.parallel&&model.concurrency?`最多并发 ${model.concurrency} ${model.batched?'批':'个'}`:'',model.batched&&model.batchSize?`每批最多 ${model.batchSize} 个节点`:'',model.elapsed?`耗时 ${model.elapsed}`:'',model.repaired?`修正 ${model.repaired} 次`:''].filter(Boolean);
  const batchCounts=model.batched?`<div class="studio-progress-counts studio-progress-batches">${!model.batchTotal&&model.status==='completed'?'<span>批次 <b>全部复用已有优化</b></span>':`<span>已完成批次 <b>${model.batchCurrent} / ${model.batchTotal}</b></span><span>运行批次 <b>${model.runningBatches}</b></span>`}</div>`:'';
  return `<div class="studio-production-progress" role="status" aria-live="polite" data-progress-status="${escape(model.status)}"><div class="studio-progress-heading"><span>${escape(model.title)}</span><small>${escape(model.statusLabel)}</small></div><progress ${model.total?`value="${model.current}" max="${model.total}"`:''} aria-label="${escape(model.total?`${model.batched?'已完成节点':model.parallel?'已完成':'进度'} ${model.current} / ${model.total}`:model.title)}"></progress>${model.total||model.parallel?`<div class="studio-progress-counts">${model.metrics.map(metric=>`<span>${escape(metric.label)} <b>${escape(metric.value)}</b></span>`).join('')}</div>`:''}${batchCounts}${detail.length?`<small class="studio-progress-detail">${detail.map(escape).join(' · ')}</small>`:''}</div>`;
}

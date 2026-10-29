import assert from 'node:assert/strict';
import test from 'node:test';
import { selectProductionProgress, productionProgressModel, renderProductionProgress, formatProgressDuration } from '../public/studio-progress.js';

test('selects the current text stage rather than stale rendering progress',()=>{
  const extraction={title:'拆解场 5-2'},optimization={title:'优化角色提示词'},render={title:'生成旧视频'};
  const studio={extraction_progress:extraction,prompt_optimization_progress:optimization,production_progress:render};
  for(const stage of ['assets-catalog-5','assets-5','assets-scenes'])assert.equal(selectProductionProgress({...studio,stage}),extraction);
  for(const stage of ['prompt-optimization','canvas'])assert.equal(selectProductionProgress({...studio,stage}),optimization);
  assert.equal(selectProductionProgress({...studio,stage:'render'}),render);
  assert.equal(selectProductionProgress({...studio,stage:'episode-1'}),null);
});

test('shows completed tasks independently from active and queued requests',()=>{
  const progress={title:'正在拆解场次',current:8,total:20,running:3,queued:8,failed:1,repaired:2,concurrency:3,elapsed_seconds:125,status:'running'};
  const model=productionProgressModel(progress,'running');
  assert.equal(model.current,8);
  assert.equal(model.running,3);
  assert.equal(model.queued,8);
  assert.equal(model.failed,1);
  const html=renderProductionProgress(progress,'running');
  assert.match(html,/value="8" max="20"/);
  assert.match(html,/已完成 <b>8 \/ 20<\/b>/);
  assert.match(html,/运行中 <b>3<\/b>/);
  assert.match(html,/排队 <b>8<\/b>/);
  assert.match(html,/需处理 <b>1<\/b>/);
  assert.match(html,/最多并发 3 个/);
  assert.match(html,/耗时 2 分 5 秒/);
  assert.match(html,/修正 2 次/);
});

test('finished stage cannot keep stale workers, queue, or failures visible',()=>{
  const model=productionProgressModel({current:12,total:12,running:3,queued:9,failed:1,status:'completed'},'running');
  assert.equal(model.statusLabel,'已完成');
  assert.equal(model.running,0);
  assert.equal(model.queued,0);
  assert.equal(model.failed,0);
});

test('batch optimization distinguishes nodes from concurrent requests',()=>{
  const progress={title:'正在批量优化提示词',current:6,total:24,running:12,queued:6,failed:0,concurrency:3,
    batch_current:1,batch_total:4,batch_size:6,running_batches:2,status:'running'};
  const html=renderProductionProgress(progress,'running');
  assert.match(html,/value="6" max="24"/);
  assert.match(html,/已完成节点 <b>6 \/ 24<\/b>/);
  assert.match(html,/运行节点 <b>12<\/b>/);
  assert.match(html,/排队节点 <b>6<\/b>/);
  assert.match(html,/已完成批次 <b>1 \/ 4<\/b>/);
  assert.match(html,/运行批次 <b>2<\/b>/);
  assert.match(html,/最多并发 3 批 · 每批最多 6 个节点/);
  assert.doesNotMatch(html,/最多并发 3 个/);
  const completed=productionProgressModel({...progress,current:24,batch_current:4,status:'completed'},'completed');
  assert.equal(completed.runningBatches,0);
  assert.equal(completed.running,0);
  assert.equal(completed.queued,0);
  assert.equal(completed.batchCurrent,4);
  const cached=renderProductionProgress({...progress,current:24,batch_current:0,batch_total:0,status:'completed'},'completed');
  assert.match(cached,/全部复用已有优化/);
  assert.doesNotMatch(cached,/已完成批次 <b>0 \/ 0/);
});

test('pause and blocking remain visible while preserving completed work',()=>{
  const progress={current:8,total:20,running:0,queued:11,failed:1,status:'running'};
  assert.equal(productionProgressModel(progress,'paused').statusLabel,'已暂停');
  assert.equal(productionProgressModel(progress,'blocked').statusLabel,'需要处理');
  assert.equal(productionProgressModel(progress,'blocked').current,8);
  assert.equal(productionProgressModel({...progress,status:'completed'},'blocked').statusLabel,'已完成');
});

test('legacy progress has no fabricated queue or completed count semantics',()=>{
  const html=renderProductionProgress({current:2,total:6,title:'生成视频'},'running');
  assert.match(html,/进度 <b>2 \/ 6<\/b>/);
  assert.doesNotMatch(html,/运行中|排队|并发|已完成/);
  const preparing=renderProductionProgress(selectProductionProgress({stage:'assets-catalog-5'}),'running');
  assert.match(preparing,/正在统一角色、场景与道具/);
  assert.doesNotMatch(preparing,/value=|max=|NaN|undefined/);
});

test('untrusted model titles and unexpected counters do not become HTML',()=>{
  const html=renderProductionProgress({title:'<img src=x onerror="alert(1)">',status:'" onmouseover="x',current:-2,total:'bad',running:Infinity,concurrency:3},'running');
  assert.doesNotMatch(html,/<img| onerror="|data-progress-status=""/);
  assert.match(html,/&lt;img/);
  assert.doesNotMatch(html,/NaN|Infinity|value="-/);
  assert.equal(formatProgressDuration(3660),'1 小时 1 分');
});

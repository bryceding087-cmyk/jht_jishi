const TYPES = {video:'视频',image:'图片',audio:'音频',agent:'Agent',render:'合成'};
const STATES = {waiting:'等待名额',credentials:'等待授权',executing:'正在提交',provider:'供应商处理中',unknown:'结果待确认',queued:'排队中',running:'生成中',waiting_tool:'执行中',done:'已完成',succeeded:'已完成',completed:'已完成',failed:'失败',blocked:'已暂停',cancelled:'已停止',paused:'已暂停'};
const timeText = value => value ? new Date(Number(value)*1000).toLocaleString('zh-CN',{hour12:false}) : '—';

export function createAdminLogs({container,api,escapeHtml:esc,onBack}) {
  let visible=false,tab='operations',page=1,timer,revision=0,rows=[],lastTotal=0,detailRevision=0;
  const badge = value => `<span class="admin-log-badge ${['failed','blocked','unknown'].includes(value)?'is-warning':['done','succeeded','completed','success'].includes(value)?'is-success':''}">${esc(STATES[value]||({success:'成功'}[value])||value||'—')}</span>`;
  const identity = actor => `<strong>${esc(actor?.name||actor?.display_name||actor?.username||'未登录')}</strong><small>${esc(actor?.username||actor?.id||'')}${actor?.organization?' · '+esc(actor.organization):''}</small>`;
  const field = (name,value) => `<div><dt>${esc(name)}</dt><dd>${esc(value===undefined||value===null||value===''?'—':value)}</dd></div>`;
  container.innerHTML=`<div class="admin-log-heading"><div><p class="admin-log-eyebrow">管理中心</p><h1>系统日志</h1><p>查看用户操作、生成请求和任务队列</p></div><button type="button" class="secondary" data-log-back>返回创作</button></div>
    <div class="admin-log-summary" aria-label="全系统任务概况" aria-live="polite"></div>
    <div class="admin-log-toolbar"><div class="admin-log-tabs" role="tablist" aria-label="日志类别"><button type="button" role="tab" id="logOperationsTab" aria-controls="adminLogResults" aria-selected="true" data-log-tab="operations">用户操作</button><button type="button" role="tab" id="logTasksTab" aria-controls="adminLogResults" aria-selected="false" data-log-tab="tasks">生成任务</button></div><div class="admin-log-refresh"><label><input type="checkbox" data-log-auto checked>每 10 秒刷新</label><button class="secondary" type="button" data-log-refresh>刷新</button></div></div>
    <form class="admin-log-filters"><label>用户<select name="user_id"><option value="">全部用户</option></select></label><label class="admin-log-search">请求编号 / 操作<input name="q" type="search" maxlength="200" placeholder="搜索请求编号、任务 ID 或操作"></label><label data-log-operation-filter>操作结果<select name="outcome"><option value="">全部结果</option><option value="success">成功</option><option value="failed">失败</option></select></label><label data-log-task-filter hidden>任务类型<select name="task_type"><option value="">全部类型</option>${Object.entries(TYPES).map(([key,value])=>`<option value="${key}">${value}</option>`).join('')}</select></label><label data-log-task-filter hidden>任务状态<select name="status"><option value="">全部状态</option>${['queued','running','done','succeeded','failed','blocked','cancelled'].map(value=>`<option value="${value}">${STATES[value]}${value==='succeeded'?'（图片 / 音频）':''}</option>`).join('')}</select></label><label data-log-task-filter hidden>队列状态<select name="queue_state"><option value="">全部队列</option><option value="active">全部未结束任务</option>${['waiting','credentials','executing','provider','unknown','done','failed','cancelled'].map(value=>`<option value="${value}">${STATES[value]}</option>`).join('')}</select></label><label>开始时间<input type="datetime-local" name="since"></label><label>结束时间<input type="datetime-local" name="until"></label><div class="admin-log-filter-actions"><button class="primary" type="submit">查询</button><button class="secondary" type="button" data-log-reset>重置</button></div></form>
    <p class="admin-log-feedback" role="status" aria-live="polite"></p><div class="admin-log-results" id="adminLogResults" role="tabpanel" aria-labelledby="logOperationsTab"></div>
    <div class="admin-log-pagination"><span data-log-count></span><div><button type="button" class="secondary" data-log-prev>上一页</button><span data-log-page>第 1 页</span><button type="button" class="secondary" data-log-next>下一页</button></div></div>
    <p class="admin-log-footnote">操作与状态变化从本功能启用后开始记录；已有生成任务可直接查询。</p>
    <dialog class="admin-log-detail" aria-labelledby="adminLogDetailTitle"><div class="admin-log-detail-heading"><h2 id="adminLogDetailTitle">日志详情</h2><button type="button" class="secondary" data-log-close aria-label="关闭详情">关闭</button></div><div data-log-detail-body></div></dialog>`;
  const form=container.querySelector('form'),results=container.querySelector('.admin-log-results'),feedback=container.querySelector('.admin-log-feedback'),dialog=container.querySelector('dialog');
  let applied=new URLSearchParams();

  function summary(data) {
    const cards=[['视频并发',`${data.video_active} / ${data.video_limit}`,`每个账号最多 ${data.video_account_limit} 个`],['等待提交',data.video_waiting,'等待名额或重新授权'],['结果待确认',data.unknown,'需要核对供应商记录'],['近 24 小时异常',data.failed_24h,'失败或暂停的生成任务']];
    container.querySelector('.admin-log-summary').innerHTML=cards.map(([label,value,note])=>`<article><span>${esc(label)}</span><strong>${esc(value)}</strong><small>${esc(note)}</small></article>`).join('');
  }
  function render(data) {
    rows=data.items;lastTotal=data.total;
    const head=tab==='operations'?['时间','用户','操作','请求 / 资源编号','结果','耗时','']:['创建时间','用户','生成请求','任务状态','队列状态','请求 / 任务编号',''];
    const body=rows.map((item,index)=>tab==='operations'
      ?`<tr><td class="admin-log-time">${esc(timeText(item.created_at))}</td><td>${identity(item.actor)}</td><td><strong>${esc(item.action)}</strong><small>${esc(item.method)} ${esc(item.route)}</small></td><td class="admin-log-id">${esc(item.request_id||item.trace_id)}<small>${esc(item.resource_id||'')}</small></td><td>${badge(item.status)}<small>HTTP ${Number(item.http_status)}</small></td><td>${Number(item.duration_ms)} ms</td><td><button type="button" class="admin-log-link" data-log-detail="${index}">详情</button></td></tr>`
      :`<tr><td class="admin-log-time">${esc(timeText(item.created_at))}</td><td>${identity(item.actor)}</td><td><strong>${esc(TYPES[item.type])} · ${esc(item.model||'—')}</strong><small class="admin-log-prompt" title="${esc(item.prompt)}">${esc(item.prompt||'无提示词')}</small></td><td>${badge(item.submit_unknown?'unknown':item.status)}${item.deleted?'<small>记录已删除</small>':''}</td><td>${badge(item.queue_state)}</td><td class="admin-log-id">${esc(item.request_id||item.id)}<small>${esc(item.provider_task_id||item.id)}</small></td><td><button type="button" class="admin-log-link" data-log-detail="${index}">详情</button></td></tr>`).join('');
    results.innerHTML=rows.length?`<div class="admin-log-table-wrap"><table><thead><tr>${head.map(label=>`<th scope="col">${label}</th>`).join('')}</tr></thead><tbody>${body}</tbody></table></div>`:'<div class="admin-log-empty"><strong>没有匹配的记录</strong><p>调整筛选条件，或等待新的操作和生成任务。</p></div>';
    container.querySelector('[data-log-count]').textContent=`共 ${data.total} 条记录`;
    container.querySelector('[data-log-page]').textContent=`第 ${page} / ${Math.max(1,Math.ceil(data.total/30))} 页`;
    container.querySelector('[data-log-prev]').disabled=page<=1;
    container.querySelector('[data-log-next]').disabled=page*30>=data.total;
  }
  async function refresh() {
    clearTimeout(timer);
    if(!visible)return;
    const current=++revision,params=new URLSearchParams(applied);
    params.set('page',page);params.set('page_size','30');
    results.setAttribute('aria-busy','true');
    results.querySelectorAll('button').forEach(button=>button.disabled=true);
    feedback.textContent='正在读取日志…';
    try {
      const [data,stats]=await Promise.all([api(`/api/admin/logs/${tab}?${params}`,{background:true}),api('/api/admin/logs/summary',{background:true})]);
      if(!visible||current!==revision)return;
      if(page>1&&data.total<=(page-1)*30){page=Math.max(1,Math.ceil(data.total/30));return refresh();}
      render(data);summary(stats);feedback.textContent=`更新于 ${timeText(stats.updated_at)}`;feedback.classList.remove('is-error');
    } catch(error) {
      if(visible&&current===revision){feedback.textContent=`日志读取失败：${error.message}`;feedback.classList.add('is-error');}
    } finally {
      if(visible&&current===revision){results.removeAttribute('aria-busy');results.querySelectorAll('button').forEach(button=>button.disabled=false);if(container.querySelector('[data-log-auto]').checked)timer=setTimeout(refresh,10000);}
    }
  }
  function applyFilters() {
    const values=new FormData(form),params=new URLSearchParams();
    for(const name of ['user_id','q',...(tab==='operations'?['outcome']:['task_type','status','queue_state'])])if(values.get(name))params.set(name,values.get(name));
    for(const name of ['since','until'])if(values.get(name))params.set(name,String(new Date(values.get(name)).getTime()/1000));
    if(params.has('since')&&params.has('until')&&Number(params.get('since'))>Number(params.get('until'))){feedback.textContent='开始时间不能晚于结束时间';feedback.classList.add('is-error');return;}
    applied=params;page=1;void refresh();
  }
  const actorFields=actor=>field('用户',actor?.name||actor?.display_name)+field('账号',actor?.username)+field('用户 ID',actor?.id)+field('角色',actor?.role==='admin'?'管理员':actor?.role==='user'?'成员':'未登录')+field('组织 / 部门',actor?.organization)+field('工号',actor?.work_number);
  const operationFields=item=>`<dl class="admin-log-fields">${actorFields(item.actor)}${field('操作',item.action)}${field('时间',timeText(item.created_at))}${field('来源 IP',item.address)}${field('接口',`${item.method} ${item.route}`)}${field('HTTP 状态',item.http_status)}${field('耗时',`${item.duration_ms} ms`)}${field('操作追踪号',item.trace_id)}${field('生成请求编号',item.request_id)}${field('任务 ID',item.task_id)}${field('资源 ID',item.resource_id)}${field('项目 / 对话 ID',item.project_id)}${item.attempted_username?field('尝试登录的账号',item.attempted_username):''}</dl>${item.error?`<h3>错误信息</h3><pre>${esc(item.error)}</pre>`:''}`;
  async function showDetail(index) {
    const item=rows[index];if(!item)return;
    const current=++detailRevision,body=container.querySelector('[data-log-detail-body]');
    body.textContent='正在读取详情…';dialog.showModal();
    if(tab==='operations'){body.innerHTML=operationFields(item);return;}
    try {
      const data=await api(`/api/admin/logs/tasks/${encodeURIComponent(item.kind)}/${encodeURIComponent(item.id)}`,{background:true});
      if(current!==detailRevision||!dialog.open)return;
      const task=data.task;
      body.innerHTML=`<dl class="admin-log-fields">${actorFields(task.actor)}${field('任务 ID',task.id)}${field('请求编号',task.request_id)}${field('供应商任务 ID',task.provider_task_id)}${field('类型',TYPES[task.type])}${field('模型',task.model)}${field('任务状态',STATES[task.status]||task.status)}${field('队列状态',STATES[task.queue_state]||task.queue_state)}${field('创建时间',timeText(task.created_at))}${field('提交时间',timeText(task.submitted_at))}${field('完成时间',timeText(task.completed_at))}${field('项目 / 对话 ID',task.project_id)}${field('所属用户 ID',task.owner_id)}${field('队列执行账号',task.queue.user_id)}${field('入队序号',task.queue.sequence)}${field('开始执行',timeText(task.queue.started_at))}${field('最近进展',timeText(task.queue.progress_at))}${field('超时释放',timeText(task.queue.timed_out_at))}${Object.entries(task.parameters).map(([key,value])=>field(({mode:'生成模式',resolution:'清晰度',duration:'时长（秒）',aspect_ratio:'画面比例',size:'尺寸',count:'数量',model_config_id:'模型配置 ID',node_id:'节点 ID'})[key]||key,value)).join('')}</dl>${task.status_message?`<p>${esc(task.status_message)}</p>`:''}${task.error?`<h3>错误信息</h3><pre>${esc(task.error)}</pre>`:''}<h3>生成提示词</h3><pre>${esc(task.prompt||'—')}</pre><h3>任务与队列变化</h3>${data.events.length?`<ol class="admin-log-timeline">${data.events.map(event=>`<li><time>${esc(timeText(event.created_at))}</time><strong>${esc(event.action)}</strong>${Object.entries(event.changes).map(([key,value])=>`<p>${esc(({status:'任务状态',state:'队列状态',provider_task_id:'供应商任务 ID',submit_unknown:'结果待确认',error:'错误',local_stop_requested:'停止请求'})[key]||key)}：${esc(STATES[value.from]||value.from||'—')} → ${esc(STATES[value.to]||value.to||'—')}</p>`).join('')}</li>`).join('')}</ol>`:'<p class="admin-log-muted">暂无状态变化日志。历史任务从本功能启用后的下一次状态变化开始记录。</p>'}${data.events.length===200?'<p class="admin-log-muted">显示最近 200 条状态变化。</p>':''}${data.operations.length?`<h3>关联用户操作</h3>${data.operations.map(operationFields).join('')}`:''}`;
    }catch(error){if(current===detailRevision&&dialog.open)body.textContent=`读取详情失败：${error.message}`;}
  }
  form.addEventListener('submit',event=>{event.preventDefault();applyFilters();});
  container.querySelector('[data-log-reset]').onclick=()=>{form.reset();applyFilters();};
  container.querySelector('[data-log-refresh]').onclick=()=>void refresh();
  container.querySelector('[data-log-auto]').onchange=event=>{clearTimeout(timer);if(event.target.checked)void refresh();};
  container.querySelector('[data-log-back]').onclick=onBack;
  container.querySelector('[data-log-prev]').onclick=()=>{if(page>1){page--;void refresh();}};
  container.querySelector('[data-log-next]').onclick=()=>{if(page*30<lastTotal){page++;void refresh();}};
  container.querySelectorAll('[data-log-tab]').forEach(button=>button.onclick=()=>{
    if(tab===button.dataset.logTab)return;tab=button.dataset.logTab;
    container.querySelectorAll('[data-log-tab]').forEach(item=>item.setAttribute('aria-selected',String(item===button)));
    results.setAttribute('aria-labelledby',button.id);
    container.querySelectorAll('[data-log-task-filter]').forEach(item=>item.hidden=tab!=='tasks');
    container.querySelectorAll('[data-log-operation-filter]').forEach(item=>item.hidden=tab!=='operations');
    applyFilters();
  });
  results.addEventListener('click',event=>{const button=event.target.closest('[data-log-detail]');if(button)void showDetail(Number(button.dataset.logDetail));});
  container.querySelector('[data-log-close]').onclick=()=>dialog.close();
  dialog.addEventListener('close',()=>detailRevision++);
  return {
    async show(){
      visible=true;
      void refresh();
      try {const data=await api('/api/admin/logs/users',{background:true});if(!visible)return;
        const select=form.elements.user_id,selected=select.value;
        select.innerHTML='<option value="">全部用户</option>'+data.users.map(user=>`<option value="${esc(user.id)}">${esc(user.display_name)} · ${esc(user.username)}${user.active?'':'（已停用）'}</option>`).join('');select.value=selected;
      }catch(error){if(visible){feedback.textContent=`用户列表读取失败：${error.message}`;feedback.classList.add('is-error');}}
    },
    hide(){visible=false;revision++;detailRevision++;clearTimeout(timer);dialog.close();}
  };
}

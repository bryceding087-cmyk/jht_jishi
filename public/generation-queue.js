export function installGenerationQueue({api,json,escapeHtml:esc,toast}){
  const button=document.createElement('button');button.type='button';button.className='generation-queue-trigger';button.textContent='任务队列';button.setAttribute('aria-haspopup','dialog');document.querySelector('#sessionUser').before(button);
  const dialog=document.createElement('dialog');dialog.className='generation-queue-dialog';
  dialog.innerHTML='<header><div><h2>我的生成队列</h2><p>图片不限制并发，视频每个账号最多同时生成 10 个任务，全系统最多同时生成 100 个视频任务。连续 30 分钟无有效进展的任务自动失败并退出队列。</p></div><button type="button" data-close aria-label="关闭任务队列">×</button></header><div class="generation-queue-content" aria-live="polite"></div>';document.body.append(dialog);
  let timer,busy=false,items=[];
  const states={waiting:'排队中',credentials:'等待重新连接或配置模型',executing:'生成中',provider:'供应商处理中',unknown:'结果待核对'};
  async function refresh(){
    if(busy||!dialog.open)return;busy=true;
    try{const data=await api('/api/generation-queue',{background:true});items=data.tasks||[];
      dialog.querySelector('.generation-queue-content').innerHTML=items.length?items.map(task=>`<article><div><strong>${esc(task.title|| (task.kind==='image'?'图片生成':'视频生成'))}</strong><p>${esc(task.status_message||states[task.state]||task.state)}</p></div>${['waiting','credentials'].includes(task.state)?`<button type="button" data-cancel="${esc(task.id)}">取消排队</button>`:task.state==='unknown'?`<button type="button" data-resolve="${esc(task.id)}">核对并释放名额</button>`:''}</article>`).join(''):'<p class="generation-queue-empty">当前没有排队或生成中的任务</p>';
    }catch(error){toast(error.message);}finally{busy=false;clearTimeout(timer);if(dialog.open)timer=setTimeout(refresh,2500);}
  }
  button.onclick=()=>{dialog.showModal();refresh();};dialog.querySelector('[data-close]').onclick=()=>dialog.close();dialog.addEventListener('close',()=>clearTimeout(timer));
  const canvasButton=document.createElement('button');canvasButton.type='button';canvasButton.className='generation-queue-canvas-trigger';canvasButton.title='任务队列';canvasButton.setAttribute('aria-label','任务队列');canvasButton.innerHTML='<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M9 6h11M9 12h11M9 18h11M4 6h.01M4 12h.01M4 18h.01" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>';canvasButton.onclick=button.onclick;document.querySelector('.canvas-dock').append(canvasButton);
  dialog.addEventListener('click',async event=>{
    if(event.target===dialog){dialog.close();return;}
    const target=event.target.closest('[data-cancel],[data-resolve]');if(!target)return;
    const id=target.dataset.cancel||target.dataset.resolve,task=items.find(item=>item.id===id);if(!task)return;
    if(target.dataset.resolve&&!confirm('请先到供应商侧核对：确定这项任务已结束或没有创建成功？确认后仅释放生成名额，不会重新请求模型。'))return;
    target.disabled=true;
    try{await api(`/api/generation-queue/${task.kind==='image'?'image_task':'task'}/${encodeURIComponent(id)}/${target.dataset.resolve?'confirm-stopped':'cancel'}`,json({}));await refresh();}
    catch(error){toast(error.message);target.disabled=false;}
  });
}

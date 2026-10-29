// Trusted, finite UI bridge. No selector, code, URL or project ID comes from the model.
export function createCanvasAgentActions(api) {
  const {state,escapeHtml:esc}=api;
  const current=pid=>state.view==='canvas'&&state.project?.id===pid;
  const click=id=>document.getElementById(id)?.click();
  async function scopedPanel(pid,rid,kind){
    const context=await api.request(`/api/agent/runs/${encodeURIComponent(rid)}/canvas-context`);
    if(!current(pid)||!context.available||context.project.id!==pid)return;
    const assets=[...new Map(context.nodes.flatMap(n=>n.assets||[]).map(a=>[a.id,a])).values()];
    const rows=kind==='assets'?assets.map(a=>[a.name||'素材不可用',a.kind||'不可用']):context.nodes.filter(n=>n.generation).map(n=>[n.title||n.id,n.generation.queue_state||n.generation.status]);
    const dialog=document.createElement('dialog');dialog.className='agent-dialog canvas-tools-panel';dialog.dataset.canvasScope=pid;
    dialog.innerHTML=`<div class="agent-dialog-head"><h2>${esc(context.project.name)} · ${kind==='assets'?'画布素材':'生成状态'}</h2><button type="button" aria-label="关闭">×</button></div><div class="agent-dialog-body">${rows.length?rows.map(([name,status])=>`<p><b>${esc(name)}</b>　${esc(status)}</p>`).join(''):'<p>当前画布暂无相关内容。</p>'}${Object.values(context.omitted_details||{}).some(Boolean)?'<p>大画布仅显示部分明细，完整统计以画布概览为准。</p>':''}</div>`;
    dialog.querySelector('button').onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove(),{once:true});document.body.append(dialog);dialog.showModal();
  }
  return async function execute(pid,actions,rid){
    if(!current(pid)||state.dirty||state.saveInFlight)return;
    await api.refreshTasks();
    for(const action of actions){
      if(!current(pid))return;
      const nodes=(action.node_ids||[]).map(id=>state.nodes.find(n=>n.id===id)).filter(Boolean);
      if(action.name==='select'){
        api.updateSelection(nodes[0]?.id||null);state.selectedNodeIds=new Set(nodes.map(n=>n.id));api.syncSelection();api.refresh();
      }else if(action.name==='node_action'){
        if(!state.nodes.some(n=>n.id===action.node_id)||!['play','capture','download','polish','script','expand','reference'].includes(action.value))continue;
        api.updateSelection(action.node_id);await new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)));
        if(!current(pid)||state.selectedNodeId!==action.node_id)return;
        document.querySelector(`#nodeContextToolbar [data-context="${action.value}"]`)?.click();
      }else if(action.name==='fit'){api.fitCanvas(nodes.length?nodes:state.nodes);api.save();}
      else if(action.name==='zoom'&&Number.isFinite(action.value)&&action.value>=.05&&action.value<=8){api.setZoom(action.value);api.save();}
      else if(action.name==='pan'&&Number.isFinite(action.x)&&Number.isFinite(action.y)){state.panX=action.x;state.panY=action.y;api.setZoom(state.zoom);api.save();}
      else if(action.name==='view'){if(['workflow','storyboard'].includes(action.value))click(action.value==='workflow'?'workflowMode':'storyboardMode');}
      else if(action.name==='toggle'){
        const ids={snap:'toggleSnap',edges:'toggleEdges',minimap:'toggleMinimap'};
        const enabled=action.value==='snap'?!!state.snapToGrid:action.value==='edges'?!document.querySelector('#canvas').classList.contains('edges-hidden'):document.querySelector('#toggleMinimap')?.getAttribute('aria-pressed')==='true';
        if(ids[action.value]&&typeof action.enabled==='boolean'&&enabled!==action.enabled)click(ids[action.value]);
      }else if(action.name==='panel'){
        if(['assets','history'].includes(action.value))await scopedPanel(pid,rid,action.value);
        else if(action.value==='details'){if(state.selectedNodeId)api.openDetails();}
        else if(action.value==='add_node')api.showNodePalette();
        else if(action.value==='minimap'){if(document.querySelector('#toggleMinimap')?.getAttribute('aria-pressed')!=='true')click('toggleMinimap');}
        else if(['shortcuts','help'].includes(action.value))api.showHelp();
      }
    }
  };
}

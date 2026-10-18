export const GROUP_COLORS = [
  ['灰色','#999999'],['红色','#ef5350'],['橙色','#ff982e'],['黄色','#f6c44b'],['绿色','#2dcc79'],
  ['青色','#15b9d0'],['蓝色','#4285f4'],['紫色','#9061f9'],['粉色','#ef3394'],['白色','#dddddd'],
];
const padding = 24;
export function normalizeGroups(groups, nodes) {
  const available=new Set(nodes.map(node=>node.id)), owned=new Set(), ids=new Set();
  return (Array.isArray(groups)?groups:[]).flatMap(group=>{
    if(!group || typeof group.id!=='string' || ids.has(group.id))return [];
    ids.add(group.id);
    const members=[...new Set(Array.isArray(group.node_ids)?group.node_ids:[])].filter(id=>available.has(id)&&!owned.has(id));
    members.forEach(id=>owned.add(id));
    if(!members.length)return [];
    Object.assign(group,{title:String(group.title||'Group').slice(0,80),node_ids:members,
      color:GROUP_COLORS.some(([,color])=>color===group.color)?group.color:'#999999'});
    return [group];
  });
}
export function contentBounds(group,nodes,size) {
  const members=nodes.filter(node=>group.node_ids.includes(node.id));
  if(!members.length)return null;
  const x=Math.min(...members.map(n=>n.x))-padding, y=Math.min(...members.map(n=>n.y))-padding;
  return {x,y,width:Math.max(...members.map(n=>n.x+size(n).width))+padding-x,
    height:Math.max(...members.map(n=>n.y+size(n).height))+padding-y};
}
export function groupBounds(group,nodes,size) {
  const content=contentBounds(group,nodes,size);if(!content)return null;
  if(![group.x,group.y,group.width,group.height].every(Number.isFinite))return content;
  const x=Math.min(content.x,group.x),y=Math.min(content.y,group.y);
  return {x,y,width:Math.max(content.x+content.width,group.x+group.width)-x,
    height:Math.max(content.y+content.height,group.y+group.height)-y};
}

export function createCanvasGroups(ctx) {
  const {state,icon,escapeHtml:esc}=ctx, canvas=document.querySelector('#canvas'), stage=document.querySelector('#canvasStage');
  const toolbar=document.createElement('section');toolbar.className='workspace-chrome canvas-group-toolbar';
  toolbar.setAttribute('role','toolbar');toolbar.setAttribute('aria-label','多选与分组操作');toolbar.hidden=true;canvas.append(toolbar);
  const menu=document.createElement('section');menu.className='workspace-chrome canvas-group-menu';menu.hidden=true;canvas.append(menu);
  const frames=new Map();
  let layer=null, frame=0, toolbarKey='', menuKind='', projectId=null;
  const size=node=>ctx.nodeFootprint(node), current=()=>state.groups.find(group=>group.id===state.selectedGroupId);
  const selected=()=>state.nodes.filter(node=>state.selectedNodeIds?.has(node.id));
  function beginEdit() {ctx.history.commit();}
  function remember() {ctx.history.record();}
  function reset(){state.groups=normalizeGroups(state.groups,state.nodes);state.selectedGroupId=null;closeMenu();refresh();}
  function selectGroup(group) {
    state.selectedGroupId=group.id;state.selectedNodeIds=new Set(group.node_ids);state.selectedNodeId=null;state.selectedEdgeIndex=null;
    ctx.selectionChanged();refresh();
  }
  function groupSelected() {
    const members=selected();if(members.length<2){ctx.toast('请先框选两个或更多节点');return;}
    if(current())return;
    beginEdit();const ids=members.map(node=>node.id);
    const remaining=normalizeGroups(state.groups.map(group=>({...group,node_ids:group.node_ids.filter(id=>!ids.includes(id))})),state.nodes);
    if(remaining.length>=100){ctx.toast('画布最多支持 100 个组，请先解组或合并已有分组');return;}
    state.groups=remaining;
    let number=1;while(state.groups.some(group=>group.title===`Group ${number}`))number++;
    const group={id:crypto.randomUUID(),title:`Group ${number}`,color:'#999999',node_ids:ids};
    Object.assign(group,contentBounds(group,state.nodes,size));state.groups.push(group);selectGroup(group);remember();ctx.save();ctx.toast('已打组，拖动组框或组名可整体移动');
  }
  function ungroup() {
    const selection=new Set(state.selectedNodeIds), groups=current()?[current()]:state.groups.filter(group=>group.node_ids.every(id=>selection.has(id)));
    if(!groups.length){ctx.toast('请先选中一个组');return;}
    beginEdit();const ids=new Set(groups.map(group=>group.id));
    state.groups=state.groups.filter(group=>!ids.has(group.id));state.selectedGroupId=null;state.selectedNodeId=[...state.selectedNodeIds][0]||null;
    closeMenu();ctx.selectionChanged();remember();ctx.save();refresh();ctx.toast('已解组，节点和连线已保留');
  }
  function rename(group) {
    closeMenu();const title=frames.get(group.id)?.querySelector('.canvas-group-name');if(!title||title.querySelector('input'))return;
    const input=document.createElement('input');input.value=group.title;input.maxLength=80;input.setAttribute('aria-label','组名称');title.replaceChildren(input);
    input.onpointerdown=e=>e.stopPropagation();input.onclick=e=>e.stopPropagation();
    let cancelled=false;
    input.onkeydown=e=>{e.stopPropagation();if(e.key==='Escape'){cancelled=true;input.blur();}if(e.key==='Enter')input.blur();};
    input.onblur=()=>{const value=input.value.trim()||group.title;if(!cancelled&&value!==group.title){beginEdit();group.title=value;remember();ctx.save();}title.textContent=group.title;refresh();};
    input.focus();input.select();
  }
  function arrange(kind) {
    const group=current();if(!group)return;beginEdit();
    const nodes=state.nodes.filter(node=>group.node_ids.includes(node.id)).sort((a,b)=>a.y-b.y||a.x-b.x);
    const bounds=groupBounds(group,state.nodes,size),columns=kind==='horizontal'?nodes.length:kind==='vertical'?1:Math.ceil(Math.sqrt(nodes.length));
    const widths=Array.from({length:columns},(_,col)=>Math.max(0,...nodes.filter((_,i)=>i%columns===col).map(n=>size(n).width)));
    const heights=Array.from({length:Math.ceil(nodes.length/columns)},(_,row)=>Math.max(...nodes.slice(row*columns,(row+1)*columns).map(n=>size(n).height)));
    nodes.forEach((node,i)=>{node.x=bounds.x+padding+widths.slice(0,i%columns).reduce((sum,w)=>sum+w+32,0);node.y=bounds.y+padding+heights.slice(0,Math.floor(i/columns)).reduce((sum,h)=>sum+h+40,0);});
    Object.assign(group,contentBounds(group,state.nodes,size));ctx.renderNodes();remember();ctx.save();closeMenu();refresh();
  }
  function arrangeCanvas() {
    if(!state.groups.length)return false;
    beginEdit();const owner=new Map(), units=[];
    for(const group of state.groups){const box=groupBounds(group,state.nodes,size);if(!box)continue;units.push({id:'group:'+group.id,group,...box});group.node_ids.forEach(id=>owner.set(id,'group:'+group.id));}
    for(const node of state.nodes)if(!owner.has(node.id)){const id='node:'+node.id;owner.set(node.id,id);units.push({id,node,x:node.x,y:node.y,...size(node)});}
    const ranks=new Map(units.map(unit=>[unit.id,0])),indegree=new Map(units.map(unit=>[unit.id,0])),outgoing=new Map(units.map(unit=>[unit.id,[]])),seen=new Set();
    for(const edge of state.edges){const from=owner.get(edge.from),to=owner.get(edge.to),key=JSON.stringify([from,to]);if(!from||!to||from===to||seen.has(key))continue;seen.add(key);outgoing.get(from).push(to);indegree.set(to,indegree.get(to)+1);}
    const queue=units.filter(unit=>!indegree.get(unit.id)).map(unit=>unit.id),visited=new Set();
    for(let i=0;i<queue.length;i++){const id=queue[i];visited.add(id);for(const to of outgoing.get(id)){ranks.set(to,Math.max(ranks.get(to),ranks.get(id)+1));indegree.set(to,indegree.get(to)-1);if(!indegree.get(to))queue.push(to);}}
    const cycleRank=Math.max(0,...ranks.values())+1;for(const unit of units)if(!visited.has(unit.id))ranks.set(unit.id,cycleRank);
    let x=100;
    for(const rank of [...new Set(ranks.values())].sort((a,b)=>a-b)){
      let y=100;const column=units.filter(unit=>ranks.get(unit.id)===rank);
      for(const unit of column){
        const dx=x-unit.x,dy=y-unit.y;
        if(unit.group){for(const node of state.nodes.filter(node=>unit.group.node_ids.includes(node.id))){node.x+=dx;node.y+=dy;}Object.assign(unit.group,{x,y,width:unit.width,height:unit.height});}
        else {unit.node.x=x;unit.node.y=y;}
        y+=unit.height+100;
      }
      x+=Math.max(...column.map(unit=>unit.width))+180;
    }
    ctx.renderNodes();remember();ctx.save();ctx.fit();refresh();ctx.toast('已整理画布，分组内的相对位置已保留');return true;
  }
  function closeMenu(){menu.hidden=true;menuKind='';toolbar.querySelectorAll('[aria-expanded]').forEach(el=>el.setAttribute('aria-expanded','false'));}
  function openMenu(kind,button) {
    if(menuKind===kind)return closeMenu();const group=current();if(!group)return;
    menuKind=kind;menu.hidden=false;button.setAttribute('aria-expanded','true');
    if(kind==='color') {
      menu.setAttribute('aria-label','组颜色');menu.innerHTML=`<div class="group-colors" role="radiogroup" aria-label="组颜色">${GROUP_COLORS.map(([label,color])=>`<button type="button" role="radio" aria-checked="${group.color===color}" aria-label="${label}" data-group-color="${color}" style="--swatch:${color}">${group.color===color?icon('check'):''}</button>`).join('')}</div>`;
      menu.querySelectorAll('[data-group-color]').forEach(el=>el.onclick=()=>{beginEdit();group.color=el.dataset.groupColor;remember();ctx.save();closeMenu();refresh();});
    } else {
      menu.setAttribute('aria-label','组内排列');menu.innerHTML=[['grid','宫格排列'],['horizontal','水平排列'],['vertical','垂直排列']].map(([id,label])=>`<button type="button" data-group-arrange="${id}">${icon(id==='grid'?'grid':id==='horizontal'?'panel':'list')}<span>${label}</span></button>`).join('');
      menu.querySelectorAll('[data-group-arrange]').forEach(el=>el.onclick=()=>arrange(el.dataset.groupArrange));
    }
    positionMenu();menu.querySelector('button')?.focus({preventScroll:true});
  }
  function positionMenu(){if(menu.hidden)return;const rect=toolbar.getBoundingClientRect(),bounds=canvas.getBoundingClientRect();menu.style.left=Math.max(8,Math.min(canvas.clientWidth-menu.offsetWidth-8,rect.left-bounds.left))+'px';menu.style.top=Math.min(canvas.clientHeight-menu.offsetHeight-8,rect.bottom-bounds.top+6)+'px';}
  function startDrag(event,group,element) {
    if(event.button!==0||state.canvasTool==='hand'||state.spaceDown||event.target.closest('input')||state.drag)return;
    event.preventDefault();event.stopPropagation();selectGroup(group);closeMenu();
    beginEdit();const box=groupBounds(group,state.nodes,size),corner=event.target.dataset.groupCorner;
    const members=state.nodes.filter(node=>group.node_ids.includes(node.id)).map(node=>({node,x:node.x,y:node.y}));
    const startX=event.clientX,startY=event.clientY;let moved=false;
    state.drag={kind:'group',moved:false,pointerId:event.pointerId};
    const move=e=>{
      if(e.pointerId!==event.pointerId)return;e.stopPropagation();
      const dx=(e.clientX-startX)/state.zoom,dy=(e.clientY-startY)/state.zoom;
      if(!moved&&Math.hypot(dx,dy)*state.zoom<3)return;
      if(!moved)element.setPointerCapture(event.pointerId);
      moved=true;state.drag.moved=true;canvas.classList.add('is-moving');
      if(corner){
        const content=contentBounds(group,state.nodes,size);
        const left=corner.includes('w')?Math.min(box.x+dx,content.x):box.x;
        const top=corner.includes('n')?Math.min(box.y+dy,content.y):box.y;
        const right=corner.includes('e')?Math.max(box.x+box.width+dx,content.x+content.width):box.x+box.width;
        const bottom=corner.includes('s')?Math.max(box.y+box.height+dy,content.y+content.height):box.y+box.height;
        Object.assign(group,{x:left,y:top,width:right-left,height:bottom-top});
      } else {
        members.forEach(({node,x,y})=>{node.x=x+dx;node.y=y+dy;const card=stage.querySelector(`[data-node="${CSS.escape(node.id)}"]`);if(card){card.style.left=node.x+'px';card.style.top=node.y+'px';}});
        Object.assign(group,{...box,x:box.x+dx,y:box.y+dy});ctx.renderEdges();
      }
      refresh();ctx.refreshWorkspace();
    };
    const end=e=>{
      if(e.pointerId!==event.pointerId)return;e.stopPropagation();
      window.removeEventListener('pointermove',move,true);window.removeEventListener('pointerup',end,true);window.removeEventListener('pointercancel',end,true);element.removeEventListener('lostpointercapture',end);
      state.drag=null;canvas.classList.remove('is-moving');if(element.hasPointerCapture(event.pointerId))element.releasePointerCapture(event.pointerId);
      if(moved){remember();ctx.save();state.suppressClickUntil=performance.now()+100;}
      refresh();ctx.refreshWorkspace();
    };
    window.addEventListener('pointermove',move,true);window.addEventListener('pointerup',end,true);window.addEventListener('pointercancel',end,true);element.addEventListener('lostpointercapture',end);
  }
  function paint() {
    frame=0;
    if(projectId!==state.project?.id){projectId=state.project?.id;closeMenu();}
    state.groups=normalizeGroups(state.groups,state.nodes);
    if(state.selectedGroupId&&!current())state.selectedGroupId=null;
    if(!layer?.isConnected){layer=document.createElement('div');layer.className='canvas-groups-layer';stage.prepend(layer);frames.clear();}
    for(const [id,element]of frames)if(!state.groups.some(group=>group.id===id)){element.remove();frames.delete(id);}
    for(const group of state.groups){
      let element=frames.get(group.id);
      if(!element){
        element=document.createElement('div');element.className='canvas-group';element.dataset.canvasGroup=group.id;element.tabIndex=0;element.setAttribute('role','group');
        element.innerHTML='<div class="canvas-group-name" title="双击重命名组"></div>'+['nw','ne','sw','se'].map(corner=>`<span class="canvas-group-handle ${corner}" data-group-corner="${corner}"></span>`).join('');
        element.onpointerdown=e=>startDrag(e,group,element);
        element.onclick=e=>{e.stopPropagation();if(!e.target.closest('input'))selectGroup(group);};
        element.oncontextmenu=e=>{e.preventDefault();e.stopPropagation();selectGroup(group);};
        element.querySelector('.canvas-group-name').ondblclick=e=>{e.preventDefault();e.stopPropagation();selectGroup(group);rename(group);};
        element.onkeydown=e=>{if(e.target===element&&['Enter',' '].includes(e.key)){e.preventDefault();selectGroup(group);}};
        layer.append(element);frames.set(group.id,element);
      }
      // Handlers resolve the current model object; normalization can replace it.
      element.onpointerdown=e=>startDrag(e,group,element);element.onclick=e=>{e.stopPropagation();if(!e.target.closest('input'))selectGroup(group);};
      element.oncontextmenu=e=>{e.preventDefault();e.stopPropagation();selectGroup(group);};
      element.onkeydown=e=>{if(e.target===element&&['Enter',' '].includes(e.key)){e.preventDefault();selectGroup(group);}};
      element.querySelector('.canvas-group-name').ondblclick=e=>{e.preventDefault();e.stopPropagation();selectGroup(group);rename(group);};
      const bounds=groupBounds(group,state.nodes,size);Object.assign(group,bounds);
      Object.assign(element.style,{left:bounds.x+'px',top:bounds.y+'px',width:bounds.width+'px',height:bounds.height+'px'});
      element.style.setProperty('--group-color',group.color);element.style.setProperty('--group-label-scale',String(1/state.zoom));
      element.classList.toggle('is-selected',state.selectedGroupId===group.id);element.setAttribute('aria-label',`${group.title}，${group.node_ids.length} 个节点`);
      const title=element.querySelector('.canvas-group-name');if(!title.querySelector('input')&&title.textContent!==group.title)title.textContent=group.title;
    }
    const group=current(), nodes=selected(), visible=state.view==='canvas'&&!state.drag?.moved&&(group||nodes.length>1);
    toolbar.hidden=!visible;if(!visible){closeMenu();return;}
    const key=group?`${group.id}:${group.color}:${group.title}`:'multi:'+nodes.length;
    if(key!==toolbarKey){
      toolbarKey=key;closeMenu();
      toolbar.innerHTML=group?`<button type="button" class="group-color-trigger" data-group-action="color" aria-label="组颜色" aria-haspopup="true" aria-expanded="false"><i style="background:${group.color}"></i></button><button type="button" data-group-action="layout" aria-label="组内排列" aria-haspopup="true" aria-expanded="false">${icon('grid')}${icon('chevronDown')}</button><span class="group-toolbar-divider"></span><button type="button" data-group-action="rename">${icon('pencil')}重命名</button><button type="button" data-group-action="ungroup" title="Ctrl / Alt + Shift + G">${icon('ungroup')}解组</button>`:`<span class="group-selection-count">已选 ${nodes.length} 个节点</span><span class="group-toolbar-divider"></span><button type="button" data-group-action="group" title="Ctrl / Alt + G">${icon('group')}打组</button>`;
    }
    const bounds=group?groupBounds(group,state.nodes,size):contentBounds({node_ids:nodes.map(n=>n.id)},state.nodes,size);
    toolbar.style.left=Math.max(8,Math.min(canvas.clientWidth-toolbar.offsetWidth-8,state.panX+(bounds.x+bounds.width/2)*state.zoom-toolbar.offsetWidth/2))+'px';
    toolbar.style.top=Math.max(56,Math.min(canvas.clientHeight-toolbar.offsetHeight-76,state.panY+bounds.y*state.zoom-toolbar.offsetHeight-34))+'px';positionMenu();
  }
  function refresh(){if(!frame)frame=requestAnimationFrame(paint);}
  toolbar.onclick=e=>{const button=e.target.closest('[data-group-action]');if(!button)return;const action=button.dataset.groupAction;if(action==='group')groupSelected();else if(action==='ungroup')ungroup();else if(action==='rename')rename(current());else openMenu(action,button);};
  document.addEventListener('pointerdown',e=>{if(!e.target.closest('.canvas-group-menu,.canvas-group-toolbar'))closeMenu();});
  document.addEventListener('pointerdown',e=>{
    const input=document.activeElement;
    if(input?.matches('.canvas-group-name input')&&e.target!==input)input.blur();
  },true);
  window.addEventListener('keydown',e=>{
    if(e.isComposing||state.view!=='canvas'||!document.querySelector('#modalBackdrop').hidden||e.target.closest('input,textarea,select,[contenteditable="true"]'))return;
    if(e.code==='KeyG'&&(e.ctrlKey||e.metaKey||e.altKey)&&!(e.ctrlKey&&e.altKey)){
      e.preventDefault();e.stopImmediatePropagation();if(e.shiftKey)ungroup();else groupSelected();
    } else if(e.key==='Escape')closeMenu();
  },true);
  new ResizeObserver(refresh).observe(canvas);
  return {refresh,reset,groupSelected,ungroup,arrangeCanvas};
}

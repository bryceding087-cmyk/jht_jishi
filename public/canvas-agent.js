export function createCanvasAgent({chat,state,icon,toast,beforeOpen,contextText,refreshCanvas}) {
  const root=document.querySelector('#agentView'),home=root.parentElement,next=root.nextSibling;
  const panel=document.createElement('aside');panel.id='canvasAgentPanel';panel.className='canvas-agent-panel';panel.hidden=true;
  panel.setAttribute('aria-label','画布 Agent 悬浮对话');
  panel.innerHTML=`<div class="canvas-agent-resizer" role="separator" tabindex="0" aria-label="调整 Agent 面板宽度" aria-orientation="vertical" aria-valuemin="320" aria-valuemax="640" aria-valuenow="400"></div><button type="button" class="canvas-agent-close" aria-label="关闭 Agent 对话" title="关闭">${icon('close')}</button>`;
  document.body.append(panel);
  const welcome=document.createElement('div');welcome.className='agent-dock-welcome';welcome.hidden=true;
  welcome.innerHTML=`<span class="agent-dock-orb" aria-hidden="true"></span><h2>让建绘通辅助你的无限创意</h2><div class="agent-dock-starters">${[['sense','link','感知画布开始创作'],['story','text','从创意开始剧本原创'],['upload','upload','上传故事来改编'],['polish','sparkle','优化画布提示词']].map(([action,glyph,label])=>`<button type="button" data-dock-starter="${action}">${icon(glyph)}<span>${label}</span>${icon('arrowUpRight')}</button>`).join('')}</div>`;
  root.querySelector('#agentScroll').prepend(welcome);
  const storyFile=document.createElement('input');storyFile.type='file';storyFile.accept='.txt,.md,text/plain,text/markdown';storyFile.hidden=true;panel.append(storyFile);
  const orb=()=>document.querySelector('#workspaceAgent');
  let opened=false,operation=0,animation=null,dragging=false,width=400;
  try{width=Math.max(320,Math.min(640,Number(localStorage.getItem('jht.canvasAgentWidth'))||400));}catch{}
  function setWidth(value,save=false){
    width=Math.max(320,Math.min(640,innerWidth-48,value));
    if(innerWidth<760)width=Math.max(300,Math.min(400,innerWidth-16));
    document.body.style.setProperty('--canvas-agent-width',`${width}px`);
    panel.querySelector('[role=separator]').setAttribute('aria-valuenow',String(Math.round(width)));
    if(save)try{localStorage.setItem('jht.canvasAgentWidth',String(width));}catch{}
    refreshCanvas();
  }
  setWidth(width);
  const reduced=()=>matchMedia('(prefers-reduced-motion: reduce)').matches;
  function restore(){
    if(root.parentElement===panel)home.insertBefore(root,next);
    welcome.hidden=true;root.classList.remove('agent-docked');panel.hidden=true;panel.inert=true;
    chat.setDocked(false).catch(error=>toast(error.message));
  }
  async function open(){
    if(opened)return close();
    const projectId=state.project?.id;if(!projectId){toast('请先创建或打开一个项目');return;}
    opened=true;const ticket=++operation;
    orb()?.setAttribute('aria-expanded','true');orb()?.setAttribute('aria-controls',panel.id);
    try{
      await beforeOpen();
      if(ticket!==operation)return;
      if(state.view!=='canvas'||state.project?.id!==projectId){close(true,false);return;}
      await chat.setDocked(true,projectId);
      if(ticket!==operation)return;
      if(state.view!=='canvas'||state.project?.id!==projectId){close(true,false);return;}
      animation?.cancel();panel.append(root);welcome.hidden=false;panel.hidden=false;panel.inert=false;
      setWidth(width);document.body.classList.add('canvas-agent-open');
      const showing=chat.show();
      if(!reduced()){
        animation=panel.animate([{transform:'translateX(28px)',opacity:0},{transform:'translateX(0)',opacity:1}],{duration:240,easing:'cubic-bezier(.4,0,.2,1)'});
      }
      await Promise.all([showing,animation?.finished.catch(()=>{})]);
      if(ticket===operation){animation=null;chat.focus();refreshCanvas();}
    }catch(error){if(ticket===operation){close(true);toast(error.message);}}
  }
  function close(immediate=false,focus=true){
    opened=false;++operation;animation?.cancel();animation=null;
    document.body.classList.remove('canvas-agent-open');orb()?.setAttribute('aria-expanded','false');
    chat.hide();root.querySelectorAll('video,audio').forEach(media=>media.pause());
    if(root.parentElement!==panel){chat.setDocked(false).catch(()=>{});return;}
    const ticket=operation;panel.inert=true;
    if(immediate||reduced()){restore();refreshCanvas();if(focus)orb()?.focus({preventScroll:true});return;}
    animation=panel.animate([{transform:'translateX(0)',opacity:1},{transform:'translateX(28px)',opacity:0}],{duration:180,easing:'cubic-bezier(.4,0,.2,1)',fill:'forwards'});
    animation.finished.then(()=>{if(ticket!==operation)return;restore();animation.cancel();animation=null;refreshCanvas();if(focus)orb()?.focus({preventScroll:true});}).catch(()=>{});
  }
  panel.querySelector('.canvas-agent-close').onclick=()=>close();
  panel.addEventListener('keydown',event=>{
    if(event.key==='Escape'&&!event.defaultPrevented&&!root.querySelector('dialog[open]')){event.preventDefault();close();}
    event.stopPropagation();
  });
  const handle=panel.querySelector('.canvas-agent-resizer');
  handle.onpointerdown=event=>{if(event.button!==0)return;event.preventDefault();dragging=true;document.body.classList.add('canvas-agent-resizing');handle.setPointerCapture(event.pointerId);};
  handle.onpointermove=event=>{if(dragging)setWidth(innerWidth-16-event.clientX);};
  const finish=()=>{if(!dragging)return;dragging=false;document.body.classList.remove('canvas-agent-resizing');setWidth(width,true);};
  handle.onpointerup=finish;handle.onlostpointercapture=finish;handle.onpointercancel=finish;
  handle.onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();setWidth(event.key==='Home'?320:event.key==='End'?640:width+(event.key==='ArrowLeft'?24:-24),true);}};
  window.addEventListener('resize',()=>{if(opened)setWidth(width);});
  welcome.onclick=event=>{
    const action=event.target.closest('[data-dock-starter]')?.dataset.dockStarter;if(!action)return;
    if(action==='upload'){storyFile.click();return;}
    const context=contextText();
    chat.fillDraft(action==='sense'?`请基于当前画布继续创作：\n${context}`:action==='polish'?`请优化当前画布的提示词，保留人物、场景和主要创意：\n${context}`:'请帮我把下面的创意整理成剧本和分镜：\n');
  };
  storyFile.onchange=async()=>{
    const file=storyFile.files[0];if(!file)return;
    try{if(file.size>1024*1024)throw new Error('故事文件不能超过 1 MB');const text=await file.text();if(text.length>23000)throw new Error('故事内容过长，请缩短后再上传');chat.fillDraft(`请把下面的故事改编成视频：\n${text}`);}catch(error){toast(error.message);}finally{storyFile.value='';}
  };
  return {toggle:open,isOpen:()=>opened&&root.parentElement===panel,onView(view){if(view!=='canvas'&&(opened||root.parentElement===panel))close(true,false);},close(){close();},projectChanged(){if(opened)close(true,false);}};
}

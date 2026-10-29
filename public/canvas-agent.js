export function createCanvasAgent({chat,state,icon,toast,beforeOpen,refreshCanvas}) {
  const root=document.querySelector('#agentView'),home=root.parentElement,next=root.nextSibling;
  const panel=document.createElement('aside');panel.id='canvasAgentPanel';panel.className='canvas-agent-panel';panel.hidden=true;
  panel.setAttribute('aria-label','画布 Agent 悬浮对话');
  panel.innerHTML=`<div class="canvas-agent-resizer" role="separator" tabindex="0" aria-label="调整 Agent 面板宽度" aria-orientation="vertical" aria-valuemin="320" aria-valuemax="640" aria-valuenow="400"></div><button type="button" class="canvas-agent-close" aria-label="关闭 Agent 对话" title="关闭">${icon('close')}</button>`;
  document.body.append(panel);
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
    root.classList.remove('agent-docked');panel.hidden=true;panel.inert=true;
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
      animation?.cancel();panel.append(root);panel.hidden=false;panel.inert=false;
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
  return {toggle:open,isOpen:()=>opened&&root.parentElement===panel,onView(view){if(view!=='canvas'&&(opened||root.parentElement===panel))close(true,false);},close(){close();},projectChanged(){if(opened)close(true,false);}};
}

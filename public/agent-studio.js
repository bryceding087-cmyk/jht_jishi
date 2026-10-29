import { renderMarkdown } from './markdown.js?v=agent-docs-1';
import { normalizeManuscriptNumbering } from './manuscript-numbering.js?v=1';
import { createSelectionIndicator } from './home-preferences.js?v=selection-motion-10';
import { userStorageKey } from './session.js?v=market-sdk-3';
import { selectProductionProgress, renderProductionProgress } from './studio-progress.js?v=batch-2';
import { createStoryboard, storyboardItems } from './storyboard.js?v=video-cover-1';

function createChatResizer(root,workspace){
  const handle=document.createElement('div');handle.className='studio-chat-resizer';handle.hidden=true;
  handle.setAttribute('role','separator');handle.tabIndex=0;handle.setAttribute('aria-orientation','vertical');
  handle.setAttribute('aria-label','调整对话栏宽度');handle.setAttribute('aria-controls','agentScroll agentComposer');
  handle.title='拖动调整对话栏宽度，双击恢复默认';workspace.after(handle);
  let preferred=null,drag=null;
  const key=userStorageKey('jht.studio.chatWidth');
  try{const stored=Number(localStorage.getItem(key));if(Number.isFinite(stored)&&stored>=300)preferred=stored;}catch{}
  const defaultWidth=()=>innerWidth<=980?310:370;
  const bounds=()=>({min:300,max:Math.max(300,Math.min(800,root.clientWidth-(root.clientWidth<900?320:480)-6))});
  function apply(value){
    const {min,max}=bounds(),width=Math.round(Math.max(min,Math.min(max,value)));
    root.style.setProperty('--studio-chat-width',width+'px');
    handle.setAttribute('aria-valuemin',String(min));handle.setAttribute('aria-valuemax',String(max));handle.setAttribute('aria-valuenow',String(width));
    handle.setAttribute('aria-valuetext',width+' 像素');return width;
  }
  function sync(){if(!handle.hidden&&root.clientWidth)apply(preferred??defaultWidth());}
  function remember(){try{if(preferred===null)localStorage.removeItem(key);else localStorage.setItem(key,String(preferred));}catch{}}
  function finish(){
    if(!drag)return;const pointerId=drag.pointerId;drag=null;
    document.body.classList.remove('studio-chat-resizing');
    if(handle.hasPointerCapture(pointerId))handle.releasePointerCapture(pointerId);
    remember();
  }
  handle.addEventListener('pointerdown',event=>{
    if(event.button!==0||!event.isPrimary)return;
    event.preventDefault();handle.focus({preventScroll:true});
    drag={x:event.clientX,width:Number(handle.getAttribute('aria-valuenow')),pointerId:event.pointerId};
    handle.setPointerCapture(event.pointerId);document.body.classList.add('studio-chat-resizing');
  });
  handle.addEventListener('pointermove',event=>{if(drag&&event.pointerId===drag.pointerId)preferred=apply(drag.width+drag.x-event.clientX);});
  for(const event of ['pointerup','pointercancel','lostpointercapture'])handle.addEventListener(event,finish);
  window.addEventListener('blur',finish);
  handle.addEventListener('dblclick',()=>{preferred=null;sync();remember();});
  handle.addEventListener('keydown',event=>{
    if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
    event.preventDefault();event.stopPropagation();const {min,max}=bounds();
    preferred=apply(event.key==='Home'?min:event.key==='End'?max:Number(handle.getAttribute('aria-valuenow'))+(event.key==='ArrowLeft'?24:-24));remember();
  });
  new ResizeObserver(sync).observe(root);
  return {show(visible){handle.hidden=!visible;if(!visible)finish();else sync();}};
}

// The chat remains mounted: polling, draft text and market authentication are
// unchanged when the writing workspace opens or the user visits the canvas.
export function createAgentStudio({root,api,json,esc,icon,toast,onSnapshot,onAction,onOpenCanvas}) {
  const workspace=document.createElement('section');workspace.className='agent-studio-workspace';workspace.hidden=true;
  workspace.setAttribute('aria-label','剧本创作工作区');
  root.querySelector('.agent-scroll').before(workspace);
  const resizer=createChatResizer(root,workspace);
  let run=null,selected='bible',page='script',editing=null,editorObserver=null,seen='',busy=false;
  const labels={bible:'故事设定',outline:'分集大纲',episodes:'剧本正文'};
  const active=()=>['running','waiting_tool'].includes(run?.status);
  const docs=()=>run?.studio?.documents||{};
  const tab=()=>selected.startsWith('episode-')?'episodes':selected;
  const status=d=>d?.status==='current'?'已完成':d?.status==='stale'?'待同步':'待创作';
  const episodeCount=()=>run?.studio?.brief?.episode_count||0;
  function visibility(docked=false){
    const visible=!!run?.studio?.active&&!docked;
    workspace.hidden=!visible;root.classList.toggle('studio-active',visible);resizer.show(visible);
    return visible;
  }
  let directory,docList,tabNav,editorHead,scriptBody,productionBody;
  let pageSlider,docSlider,tabSlider,listCount=-1,bodyStamp='';
  let board,boardData=null,boardLoading=null,boardFetchedAt=0,boardVersion=0;
  function mount(){
    if(directory)return;
    workspace.innerHTML=`<aside class="studio-directory"><div class="studio-project-title">${icon('script')}<span></span></div><p class="studio-section-label">创作目录</p><button class="studio-directory-section" data-studio-page="script">${icon('text')}剧本创作<span></span></button><div class="studio-document-list"></div><button class="studio-directory-section" data-studio-page="production">${icon('grid')}制作与成片${icon('chevronRight')}</button></aside>
      <section class="studio-editor"><header class="studio-editor-head"><div class="studio-heading"><span class="studio-kicker"></span><h2></h2></div><div class="studio-view-switch" hidden></div><button class="studio-quiet studio-mobile-stage"></button></header><nav class="studio-document-tabs" role="tablist" aria-label="剧本阶段">${Object.entries(labels).map(([key,label])=>`<button role="tab" data-studio-tab="${key}" aria-selected="false" tabindex="-1">${label}</button>`).join('')}</nav><div class="studio-script-content"></div><div class="studio-production-pane" hidden></div></section>`;
    directory=workspace.querySelector('.studio-directory');docList=workspace.querySelector('.studio-document-list');
    tabNav=workspace.querySelector('.studio-document-tabs');editorHead=workspace.querySelector('.studio-editor-head');
    scriptBody=workspace.querySelector('.studio-script-content');productionBody=workspace.querySelector('.studio-production-pane');
    const boardHost=document.createElement('div');productionBody.append(boardHost);
    board=createStoryboard(boardHost,{esc,icon,viewSwitchHost:editorHead.querySelector('.studio-view-switch'),onError:error=>toast(error.message),onCanvas:()=>openBoardCanvas(),onSelect:id=>openBoardCanvas(id),
      onAction:(action,button)=>onAction(action,{},button)});
    pageSlider=createSelectionIndicator(directory,':scope > [data-studio-page][aria-current=true]');
    tabSlider=createSelectionIndicator(tabNav,'[aria-selected=true]');
  }
  function draw(){
    if(!run?.studio?.active||workspace.hidden||editing)return;
    mount();
    const d=docs()[selected];
    directory.querySelector('.studio-project-title span').textContent=run.title;
    directory.querySelector('[data-studio-page=script] span').textContent=Object.keys(docs()).length;
    directory.querySelectorAll('[data-studio-page]').forEach(button=>{
      const current=button.dataset.studioPage===page;
      button.setAttribute('aria-current',String(current));button.classList.toggle('selected',current);
    });
    if(listCount!==episodeCount()){
      listCount=episodeCount();docSlider?.destroy();
      docList.innerHTML=['bible','outline'].map(key=>`<button data-studio-doc="${key}">${icon('text')}<span>${labels[key]}</span><i></i></button>`).join('')+`<p class="studio-section-label">剧本正文 · ${episodeCount()} 集</p>`+Array.from({length:episodeCount()},(_,i)=>`<button data-studio-doc="episode-${i+1}"><span class="studio-episode-number">${String(i+1).padStart(2,'0')}</span><span class="studio-doc-title"></span><i></i></button>`).join('');
      docSlider=createSelectionIndicator(docList,'[aria-current=true]');
    }
    docList.querySelectorAll('[data-studio-doc]').forEach(button=>{
      const key=button.dataset.studioDoc,doc=docs()[key];
      button.setAttribute('aria-current',String(page==='script'&&selected===key));
      const dot=button.querySelector('i');dot.className=doc?.status||'';dot.title=status(doc);
      if(key.startsWith('episode-')){
        button.title=doc?.title||'第 '+key.split('-')[1]+' 集';
        button.querySelector('.studio-doc-title').textContent=doc?.title?.replace(/^第\s*\d+\s*集\s*/,'')||button.title;
      }
    });
    editorHead.querySelector('.studio-kicker').textContent=page==='script'?'STORY STUDIO':'PRODUCTION';
    editorHead.querySelector('h2').textContent=page==='script'?'剧本':'故事板';
    editorHead.dataset.page=page;
    editorHead.querySelector('.studio-view-switch').hidden=page!=='production';
    const stageSwitch=editorHead.querySelector('.studio-mobile-stage');
    stageSwitch.textContent=page==='script'?'制作与成片':'返回剧本';stageSwitch.dataset.studioPage=page==='script'?'production':'script';
    tabNav.hidden=scriptBody.hidden=page!=='script';productionBody.hidden=page!=='production';
    tabNav.querySelectorAll('[data-studio-tab]').forEach(button=>{
      const current=button.dataset.studioTab===tab();button.setAttribute('aria-selected',String(current));button.tabIndex=current?0:-1;
    });
    const nextBody=JSON.stringify([run.id,selected,d||null,d?null:active()]);
    if(page==='script'&&bodyStamp!==nextBody){
      bodyStamp=nextBody;
      scriptBody.innerHTML=`<div class="studio-document-toolbar"><span>${esc(d?.title||labels[selected]||'第 '+selected.split('-')[1]+' 集')} ${d?`<small>v${d.version} · ${status(d)}</small>`:''}</span>${d?`<div><button class="studio-quiet" data-studio-versions title="查看旧版本">${icon('clock')}</button><button class="studio-quiet" data-studio-download title="下载稿件">${icon('download')}</button><button class="studio-quiet" data-studio-edit>${icon('pencil')}编辑</button></div>`:''}</div><div class="studio-document-scroll">${d?.status==='stale'?'<p class="studio-stale-note">上游稿件已修改，本稿待同步。可向右侧助手描述需要同步的范围。</p>':''}<article class="studio-document"></article></div>`;
      const article=scriptBody.querySelector('.studio-document');
      if(d)article.append(renderMarkdown(normalizeManuscriptNumbering(d.content)));else article.innerHTML=`<div class="studio-empty">${icon('script')}<h3>${active()?'正在把灵感写成故事':'这一篇还未开始'}</h3>${active()?'':'<p>在右侧告诉助手你的想法，或继续完善已有设定。</p>'}</div>`;
    }
    const editButton=scriptBody.querySelector('[data-studio-edit]');if(editButton)editButton.disabled=active();
    if(page==='production'){
      drawProduction();
    }
    pageSlider.update();docSlider.update();tabSlider.update();
  }
  function provisionalItems(){
    const s=run.studio;
    return (s.catalog||[]).map(a=>({id:'definition:'+a.key,category:a.type,title:a.name+(a.variant&&a.variant!=='默认'?' · '+a.variant:''),
      description:[a.facts,a.design].filter(Boolean).join('；'),status:'待创建节点',url:'',kind:'image'}));
  }
  function drawProduction(){
    if(!board||!run?.studio)return;
    const pid=run.studio.canvas_project_id,data=boardData?.id===pid?boardData:null;
    const progress=selectProductionProgress(run.studio);
    board.update({title:run.title,items:data?storyboardItems(data.project,data.assets,data.tasks):provisionalItems(),
      hasCanvas:!!pid,loading:!!boardLoading,
      actions:pid?[{id:'studio_canvas',label:'同步剧本',disabled:active()},{id:'studio_generate',label:'制作成片',primary:true,disabled:active()}]:
        [{id:'studio_canvas',label:'生成资产',primary:true,disabled:active()}],
      progress:progress?.status==='completed'?'':renderProductionProgress(progress,run.status)});
  }
  async function refreshProduction(force=false){
    const pid=run?.studio?.canvas_project_id,rid=run?.id;
    if(!pid||page!=='production'||workspace.hidden||boardLoading||!force&&Date.now()-boardFetchedAt<4000)return;
    const version=++boardVersion,operation={};boardLoading=operation;drawProduction();
    try{
      const [project,assets,tasks]=await Promise.all([api(`/api/projects/${encodeURIComponent(pid)}`,{background:true}),
        api('/api/assets',{background:true}),api(`/api/tasks?project_id=${encodeURIComponent(pid)}`,{background:true})]);
      if(version!==boardVersion||run?.id!==rid||run?.studio?.canvas_project_id!==pid)return;
      const byNode={};for(const task of [...tasks].sort((a,b)=>(a.created_at||0)-(b.created_at||0))){if(task.params?.node_id)byNode[task.params.node_id]=task;}
      boardData={id:pid,project,assets,tasks:byNode};boardFetchedAt=Date.now();
    }catch(error){if(version===boardVersion&&page==='production')toast('故事板暂未载入，可稍后重新打开。');}
    finally{if(boardLoading===operation){boardLoading=null;if(page==='production')drawProduction();}}
  }
  async function openBoardCanvas(nodeId=null){
    if(!run?.studio?.canvas_project_id)return;
    board.pause();
    await onOpenCanvas?.(run.studio.canvas_project_id,null,run.id,nodeId);
  }
  function download(content,title){const url=URL.createObjectURL(new Blob([normalizeManuscriptNumbering(content)],{type:'text/markdown;charset=utf-8'}));const a=document.createElement('a');a.href=url;a.download=title+'.md';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}
  function endEditing(){editorObserver?.disconnect();editorObserver=null;editing=null;}
  async function edit(){
    if(editing||active()||!docs()[selected])return;
    editing={key:selected,revision:run.revision,doc:docs()[selected]};
    const host=workspace.querySelector('.studio-document-scroll');host.innerHTML=`<div class="studio-edit-actions"><span>编辑完整稿件 · 保存后标记下游内容待同步</span><button class="studio-quiet" data-studio-edit-cancel>取消</button><button class="studio-primary" data-studio-edit-save>保存修改</button></div><textarea class="studio-manual-editor" aria-label="编辑剧本稿件" maxlength="60000"></textarea><p class="studio-edit-error" role="alert"></p>`;
    const editor=host.querySelector('textarea');editor.value=normalizeManuscriptNumbering(editing.doc.content);
    // Let the document pane scroll the full draft; the chat input's size limits
    // must not constrain this editor, including after dragging the divider.
    const fit=()=>{
      if(!editor.isConnected||!editor.clientWidth)return;
      const scrollTop=host.scrollTop;
      editor.style.height='0px';
      const style=getComputedStyle(editor);
      editor.style.height=(editor.scrollHeight+parseFloat(style.borderTopWidth)+parseFloat(style.borderBottomWidth))+'px';
      editor.scrollTop=0;host.scrollTop=scrollTop;
    };
    editor.addEventListener('input',fit);fit();
    let width=host.clientWidth;
    editorObserver=new ResizeObserver(()=>{if(host.clientWidth!==width){width=host.clientWidth;fit();}});
    editorObserver.observe(host);
    editor.setSelectionRange(0,0);editor.focus({preventScroll:true});host.scrollTop=0;
    workspace.querySelector('[data-studio-edit]').disabled=true;
  }
  async function versions(){
    const values=await api(`/api/agent/runs/${run.id}/documents/${encodeURIComponent(selected)}/versions`);
    const dialog=document.createElement('dialog');dialog.className='studio-versions';
    dialog.innerHTML=`<header><h3>稿件版本</h3><button class="studio-quiet" data-close aria-label="关闭">${icon('close')}</button></header><select aria-label="选择稿件版本">${values.map((v,i)=>`<option value="${i}">版本 ${v.revision} · ${esc(v.title)}</option>`).join('')}</select><article class="studio-document"></article><footer><button class="studio-primary" data-restore ${active()?'disabled':''}>恢复为新版本</button></footer>`;
    const drawVersion=()=>{const article=dialog.querySelector('article');article.replaceChildren(renderMarkdown(normalizeManuscriptNumbering(values[Number(dialog.querySelector('select').value)].content)));};
    dialog.querySelector('select').onchange=drawVersion;dialog.querySelector('[data-close]').onclick=()=>dialog.close();dialog.addEventListener('close',()=>dialog.remove(),{once:true});
    dialog.querySelector('[data-restore]').onclick=async()=>{const v=values[Number(dialog.querySelector('select').value)];try{await save(selected,v.content,v.title,run.revision);dialog.close();}catch(error){toast(error.message);}};
    root.append(dialog);drawVersion();dialog.showModal();
  }
  async function save(key,content,title,revision){
    const snapshot=await api(`/api/agent/runs/${run.id}/documents/${encodeURIComponent(key)}`,{...json({content:normalizeManuscriptNumbering(content),title,revision}),method:'PUT'});
    endEditing();seen='';bodyStamp='';await onSnapshot(snapshot);draw();
  }
  workspace.addEventListener('click',async event=>{
    const b=event.target.closest('button');if(!b||b.disabled||busy)return;
    try{
      if(b.dataset.studioDoc){if(editing)return toast('请先保存或取消当前编辑');selected=b.dataset.studioDoc;page='script';draw();}
      if(b.dataset.studioTab){if(editing)return toast('请先保存或取消当前编辑');selected=b.dataset.studioTab==='episodes'?(selected.startsWith('episode-')?selected:'episode-1'):b.dataset.studioTab;draw();}
      if(b.dataset.studioPage){if(editing)return toast('请先保存或取消当前编辑');page=b.dataset.studioPage;draw();if(page==='production')refreshProduction(true);else board?.pause();}
      if(b.hasAttribute('data-studio-edit'))edit();
      if(b.hasAttribute('data-studio-edit-cancel')){endEditing();bodyStamp='';draw();}
      if(b.hasAttribute('data-studio-edit-save')){
        busy=true;b.disabled=true;try{await save(editing.key,workspace.querySelector('textarea').value,editing.doc.title,editing.revision);}catch(error){workspace.querySelector('.studio-edit-error').textContent=error.message;}finally{busy=false;b.disabled=false;}
      }
      if(b.hasAttribute('data-studio-download'))download(docs()[selected].content,docs()[selected].title);
      if(b.hasAttribute('data-studio-versions'))await versions();
      if(b.dataset.studioAction){await onAction(b.dataset.studioAction,{},b);}
      if(b.hasAttribute('data-studio-open-canvas')||b.dataset.studioOpenEpisode)await onOpenCanvas?.(run.studio.canvas_project_id,Number(b.dataset.studioOpenEpisode)||null,run.id);
    }catch(error){toast(error.message);}
  });
  workspace.addEventListener('keydown',event=>{if(event.target.matches('[data-studio-tab]')&&['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();if(editing)return toast('请先保存或取消当前编辑');const keys=Object.keys(labels);selected=keys[(keys.indexOf(tab())+(event.key==='ArrowRight'?1:2))%3];if(selected==='episodes')selected='episode-1';draw();workspace.querySelector('[role=tab][aria-selected=true]')?.focus();}});
  return {
    update(value,docked=false){
      if(value?.id!==run?.id){endEditing();selected='bible';page='script';seen='';boardData=null;boardLoading=null;boardFetchedAt=0;++boardVersion;}
      run=value;if(!visibility(docked))return;
      const stamp=JSON.stringify([run.id,run.studio,run.status]);if(stamp!==seen){seen=stamp;draw();}if(page==='production')refreshProduction();
    },
    context(){return {focus_document:run?.studio?.documents?.[selected]?selected:'',read_only:!!editing};},
    isEditing(){return !!editing;},
    showDocument(key){selected=key;page='script';board?.pause();draw();},
    showProduction(){page='production';draw();refreshProduction(true);},
  };
}

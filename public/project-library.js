import { createViewportObserver, revealMedia } from './viewport-media.js?v=1';

// An Agent conversation belongs to the canvas collection even before its graph exists.
export function projectLibraryItems(projects=[],conversations=[],category='canvas') {
  const chats=conversations.map(item=>({...item,name:item.title||item.name||'未命名对话',library_kind:'conversation'}));
  if(category==='conversation')return chats.filter(item=>item.kind!=='agent');
  const runs=new Set(projects.map(item=>item.studio_run_id).filter(Boolean));
  return [...projects.map(item=>({...item,library_kind:'project'})),...chats.filter(item=>item.kind==='agent'&&!item.canvas_project_id&&!runs.has(item.run_id))]
    .sort((a,b)=>(b.updated_at||b.created_at||0)-(a.updated_at||a.created_at||0));
}

export function createProjectLibrary(ctx) {
  const {state,api,json,icon,escapeHtml:esc,toast}=ctx;
  const ui={category:'canvas',query:'',folder:'',folders:[],trash:false,recycled:[],recycledConversations:[],request:0,loading:false,error:''};
  const covers=createViewportObserver(revealMedia);
  const root=()=>document.querySelector('#libraryGrid');
  const date=value=>{const day=new Date(Number(value||0)*1000);return value?`${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,'0')}-${String(day.getDate()).padStart(2,'0')}`:'';};
  const dateTime=value=>value?new Date(Number(value)*1000).toLocaleString('zh-CN',{year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false}):'未记录';
  const url=value=>/^(?:https?:\/\/|\/[^/])/.test(String(value||''))?esc(value):'';
  const items=()=>projectLibraryItems(ui.trash?ui.recycled:state.projects,ui.trash?ui.recycledConversations:state.conversations,ui.category);
  function cover(project) {
    if(project.library_kind==='conversation'){
      const media=project.cover;
      if(media?.url)return media.kind==='video'?`<video data-media-src="${url(media.url)}" preload="none" muted playsinline></video>`:`<img data-media-src="${url(media.url)}" alt="" decoding="async">`;
      return `<span class="projects-empty-cover">${icon(project.kind==='audio'?'audio':project.kind==='agent'?'sparkle':project.kind==='video'?'play':'image')}</span>`;
    }
    const assets=(project.nodes||[]).map(node=>state.assets.find(asset=>asset.id===node.assetId));
    const image=assets.find(asset=>['image','prop'].includes(asset?.kind||'')&&asset.url);
    const media=image||assets.find(asset=>asset?.kind==='video'&&asset.url);
    if(media)return media.kind==='video'?`<video data-media-src="${url(media.url)}" preload="none" muted playsinline></video>`:`<img data-media-src="${url(media.url)}" alt="" decoding="async">`;
    return `<span class="projects-empty-cover">${icon('image')}</span>`;
  }
  const menu=(id,entries)=>`<details class="projects-card-menu" data-project-menu="${esc(id)}"><summary aria-label="项目操作" title="更多操作">···</summary><div role="menu">${entries.map(([action,label])=>`<button type="button" role="menuitem" data-project-action="${action}" data-id="${esc(id)}">${label}</button>`).join('')}</div></details>`;
  function draw() {
    const host=root();if(!host||state.view!=='projects')return;
    const searchFocused=document.activeElement?.id==='projectSearch',caret=document.activeElement?.selectionStart;
    const openMenu=host.querySelector('.projects-card-menu[open]')?.dataset.projectMenu;
    const focusedAction=document.activeElement?.closest('.projects-card-menu')?document.activeElement.dataset.projectAction:null;
    const folder=ui.folders.find(item=>item.id===ui.folder),query=ui.query.trim().toLocaleLowerCase();
    const projects=items().filter(project=>(ui.trash||query||!ui.folder||project.folder_id===ui.folder)&&(!query||project.name.toLocaleLowerCase().includes(query)));
    const folders=ui.category==='canvas'&&!ui.trash&&!ui.folder?ui.folders.filter(item=>!query||item.name.toLocaleLowerCase().includes(query)):[];
    host.classList.add('project-library-host');
    host.innerHTML=`<section class="projects-library" data-project-category="${ui.category}" aria-label="项目列表"><div class="projects-heading"><div class="projects-location"><div class="projects-type-tabs" role="tablist" aria-label="项目类型">${[['conversation','对话项目'],['canvas','画布项目']].map(([key,label])=>`<button type="button" role="tab" data-project-category-tab="${key}" aria-selected="${ui.category===key}" tabindex="${ui.category===key?0:-1}">${label}</button>`).join('')}</div>${ui.trash?'<h1>回收站</h1>':folder?`<button type="button" data-project-action="back" aria-label="返回画布项目">${icon('chevronLeft')}</button><h1>${esc(folder.name)}</h1>`:''}</div><div class="projects-tools"><label class="projects-search">${icon('search')}<input id="projectSearch" type="search" aria-label="搜索项目" placeholder="搜索项目" value="${esc(ui.query)}"></label><button type="button" id="projectTrashBtn" data-project-action="trash">${icon('trash')}${ui.trash?'返回项目':'回收站'}</button>${!ui.trash&&ui.category==='canvas'?`<button type="button" data-project-action="new-folder">${icon('folder')}新建文件夹</button>`:''}</div></div>
      ${ui.error?`<div class="projects-load-error" role="alert">${esc(ui.error)}<button type="button" data-project-action="retry">重新加载</button></div>`:''}
      <div class="projects-grid">${!ui.trash?`<article class="projects-tile projects-create"><button type="button" class="projects-cover" data-project-action="create">${icon('plus')}<strong>开始创作</strong></button><p>${ui.category==='conversation'?'开始新的对话':'创建新的画布项目'}</p></article>`:''}
      ${folders.map(item=>`<article class="projects-tile projects-folder"><button type="button" class="projects-cover" data-project-action="folder" data-id="${esc(item.id)}" aria-label="打开文件夹 ${esc(item.name)}">${icon('folder')}</button><div class="projects-caption"><h2>${esc(item.name)}</h2>${menu(item.id,[['rename-folder','重命名'],['delete-folder','删除文件夹']])}</div><p>${state.projects.filter(project=>project.folder_id===item.id).length} 个项目</p></article>`).join('')}
      ${projects.map(project=>`<article class="projects-tile" data-project-card="${esc(project.id)}"><button type="button" class="projects-cover" data-project-action="open" data-id="${esc(project.id)}" aria-label="打开项目 ${esc(project.name)}" ${ui.trash?'disabled':''}>${cover(project)}</button><div class="projects-caption"><h2 title="${esc(project.name)}">${esc(project.name)}</h2>${menu(project.id,ui.trash?[['restore','恢复'],['purge','永久删除']]:project.library_kind==='conversation'?[['open','打开'],['info','信息'],['rename','重命名'],['delete','移入回收站']]:[['open','打开'],['info','画布信息'],['rename','重命名'],['duplicate','复制项目'],['move','移动到文件夹'],['delete','移入回收站']])}</div><p>${date(project.updated_at||project.created_at)}</p>${ui.trash?`<button type="button" class="projects-restore" data-project-action="restore" data-id="${esc(project.id)}">恢复项目</button>`:''}</article>`).join('')}
      ${!ui.loading&&!ui.error?(ui.trash&&!projects.length?'<p class="projects-empty">回收站为空</p>':query&&!projects.length&&!folders.length?'<p class="projects-empty">没有找到匹配的项目</p>':''):''}</div></section>`;
    host.querySelector('.projects-library').setAttribute('aria-busy', String(ui.loading));
    covers.observe(host.querySelectorAll('[data-media-src]'));
    const selectCategory=category=>{ui.category=category;ui.folder='';draw();host.querySelector(`[data-project-category-tab="${category}"]`).focus({preventScroll:true});};
    host.querySelectorAll('[data-project-category-tab]').forEach(button=>button.onclick=()=>selectCategory(button.dataset.projectCategoryTab));
    host.querySelector('.projects-type-tabs').onkeydown=event=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(event.key)){event.preventDefault();selectCategory(event.key==='Home'?'conversation':event.key==='End'?'canvas':ui.category==='canvas'?'conversation':'canvas');}};
    host.querySelector('#projectSearch').oninput=event=>{ui.query=event.target.value;draw();};
    host.querySelectorAll('[data-project-action]').forEach(button=>button.onclick=()=>action(button.dataset.projectAction,button.dataset.id));
    if(openMenu){const details=host.querySelector(`[data-project-menu="${CSS.escape(openMenu)}"]`);if(details){details.open=true;if(focusedAction)details.querySelector(`[data-project-action="${CSS.escape(focusedAction)}"]`)?.focus({preventScroll:true});}}
    host.querySelectorAll('details').forEach(details=>details.ontoggle=()=>{if(details.open)host.querySelectorAll('details[open]').forEach(other=>{if(other!==details)other.open=false;});});
    if(searchFocused){const field=host.querySelector('#projectSearch');field.focus();field.setSelectionRange?.(caret,caret);}
  }
  function synchronize(project) {
    state.projects=state.projects.map(item=>item.id===project.id?project:item);
    if(state.project?.id===project.id)state.project={...state.project,...project};
    ctx.renderRecent();
  }
  async function refresh() {
    const request=++ui.request;
    ui.loading=true;ui.error='';draw();
    try {
    const quiet={background:true};
    const [projects,conversations,folders,assets,recycled,recycledConversations]=await Promise.all([api('/api/projects',quiet),api('/api/conversations',quiet),api('/api/project-folders',quiet),api('/api/assets',quiet),ui.trash?api('/api/projects?trashed=true',quiet):Promise.resolve([]),ui.trash?api('/api/conversations?trashed=true',quiet):Promise.resolve([])]);
    if(request!==ui.request)return;
    state.projects=projects;state.conversations=conversations;state.assets=assets;ui.folders=folders;ui.recycled=recycled;ui.recycledConversations=recycledConversations;
    if(ui.folder&&!folders.some(item=>item.id===ui.folder))ui.folder='';
    ctx.renderRecent();
    } catch(error) { if(request===ui.request)ui.error=error.message; }
    finally { if(request===ui.request){ui.loading=false;draw();} }
  }
  function editName(title,initial,onSave) {
    ctx.showModal(`<h2>${esc(title)}</h2><form id="projectNameForm"><label for="projectLibraryName">名称</label><input id="projectLibraryName" maxlength="80" required value="${esc(initial)}"><p id="projectLibraryError" role="alert" hidden></p><div class="modal-actions"><button type="button" class="secondary" id="projectLibraryCancel">取消</button><button type="submit" class="primary">保存</button></div></form>`);
    document.querySelector('#projectLibraryCancel').onclick=ctx.closeModal;
    document.querySelector('#projectNameForm').onsubmit=async event=>{
      event.preventDefault();const name=document.querySelector('#projectLibraryName').value.trim();if(!name)return;
      const button=event.target.querySelector('[type=submit]');button.disabled=true;
      try{await ctx.flushSave();await onSave(name);ctx.closeModal();await refresh();}catch(error){const label=document.querySelector('#projectLibraryError');label.hidden=false;label.textContent=error.message;button.disabled=false;}
    };
  }
  async function metadata(id,patch) {
    await ctx.flushSave();
    const project=state.projects.find(item=>item.id===id);
    const updated=await api(`/api/projects/${encodeURIComponent(id)}/metadata`,{...json({...patch,revision:project?.revision}),method:'PATCH'});
    synchronize(updated);
  }
  async function showConversationInfo(id){
    const info=await api(`/api/conversations/${encodeURIComponent(id)}/info`,{background:true});
    if(state.view!=='projects')return;
    ctx.showModal(`<section class="canvas-info"><h2>对话信息</h2><p class="canvas-info-name">${esc(info.title||info.name)}</p><div class="canvas-info-tab">基础信息</div><dl class="canvas-info-properties"><div><dt>类型</dt><dd>${esc({image:'图片对话',video:'视频对话',audio:'音频对话',agent:'Agent 项目'}[info.kind]||'对话')}</dd></div><div><dt>所有者</dt><dd>${esc(info.creator_name||info.creator_username||'未记录')}</dd></div><div><dt>创建时间</dt><dd>${esc(dateTime(info.created_at))}</dd></div><div><dt>最新修改</dt><dd>${esc(dateTime(info.updated_at))}</dd></div></dl></section>`,'canvas-info-modal');
  }
  function confirmConversationDelete(item,permanent=false){
    ctx.showModal(`<h2>${permanent?'永久删除对话？':'移入回收站？'}</h2><p>${esc(item.name)}</p><p>${permanent?'删除后无法恢复对话。已生成的资产仍保留。':'移入回收站后，可以恢复对话。已生成的内容会保留。'}</p><p id="conversationTrashError" role="alert" hidden></p><div class="modal-actions"><button type="button" class="secondary" id="cancelConversationTrash">取消</button><button type="button" class="primary" id="confirmConversationTrash">${permanent?'永久删除':'移入回收站'}</button></div>`);
    document.querySelector('#cancelConversationTrash').onclick=ctx.closeModal;
    document.querySelector('#confirmConversationTrash').onclick=async event=>{
      event.target.disabled=true;
      try{await api(`/api/conversations/${encodeURIComponent(item.id)}${permanent?'?permanent=true':''}`,{method:'DELETE'});if(state.activeConversationId===item.id)state.activeConversationId=null;ctx.closeModal();await refresh();toast(permanent?'对话已永久删除':'对话已移入回收站');}
      catch(error){const label=document.querySelector('#conversationTrashError');if(label){label.hidden=false;label.textContent=error.message;}event.target.disabled=false;}
    };
  }
  async function showInfo(id) {
    await ctx.flushSave();
    const info=await api(`/api/projects/${encodeURIComponent(id)}/info`,{background:true});
    if(state.view!=='projects')return;
    const owner=info.creator_name||info.creator_username||'未记录';
    const counts=info.node_counts||{};
    const categories=[['total','全部节点'],['image','图片'],['video','视频'],['audio','音频'],['text','文本'],['timeline','时间线'],['subject','主体'],['other','其他']];
    ctx.showModal(`<section class="canvas-info" aria-labelledby="canvasInfoTitle"><h2 id="canvasInfoTitle">画布信息</h2><p class="canvas-info-name">${esc(info.name)}</p><div class="canvas-info-tab">基础信息</div><dl class="canvas-info-properties"><div><dt>所有者</dt><dd class="canvas-info-owner"><span class="canvas-info-avatar" aria-hidden="true">${esc(owner==='未记录'?'?':Array.from(owner)[0])}</span><span title="${esc(info.creator_username||owner)}">${esc(owner)}</span></dd></div><div><dt>创建时间</dt><dd>${esc(dateTime(info.created_at))}</dd></div><div><dt>最新修改</dt><dd>${esc(dateTime(info.updated_at))}</dd></div></dl><h3>节点分布</h3><dl class="canvas-info-counts">${categories.map(([key,label])=>`<div${key==='total'?' class="is-total"':''}><dt>${label}${key==='total'?'<span>全部</span>':''}</dt><dd>${Number(counts[key])||0}</dd></div>`).join('')}</dl></section>`, 'canvas-info-modal');
  }
  function move(id) {
    const project=state.projects.find(item=>item.id===id);if(!project)return;
    ctx.showModal(`<h2>移动项目</h2><label for="projectMoveFolder">目标文件夹</label><select id="projectMoveFolder"><option value="">画布项目</option>${ui.folders.map(folder=>`<option value="${esc(folder.id)}" ${project.folder_id===folder.id?'selected':''}>${esc(folder.name)}</option>`).join('')}</select><div class="modal-actions"><button type="button" class="secondary" id="projectMoveCancel">取消</button><button type="button" class="primary" id="projectMoveConfirm">移动</button></div>`);
    document.querySelector('#projectMoveCancel').onclick=ctx.closeModal;
    document.querySelector('#projectMoveConfirm').onclick=async event=>{const button=event.target;button.disabled=true;try{await metadata(id,{folder_id:document.querySelector('#projectMoveFolder').value});ctx.closeModal();draw();toast('项目已移动');}catch(error){toast(error.message);button.disabled=false;}};
  }
  function deleteFolder(id) {
    const folder=ui.folders.find(item=>item.id===id);if(!folder)return;
    ctx.showModal(`<h2>删除文件夹？</h2><p>「${esc(folder.name)}」中的项目会移回画布项目。</p><div class="modal-actions"><button type="button" class="secondary" id="projectFolderCancel">取消</button><button type="button" class="primary" id="projectFolderConfirm">删除文件夹</button></div>`);
    document.querySelector('#projectFolderCancel').onclick=ctx.closeModal;
    document.querySelector('#projectFolderConfirm').onclick=async event=>{event.target.disabled=true;try{
      await ctx.flushSave();const previous=state.project;
      await api(`/api/project-folders/${encodeURIComponent(id)}`,{method:'DELETE'});
      if(ui.folder===id)ui.folder='';ctx.closeModal();await refresh();
      const latest=state.projects.find(project=>project.id===previous?.id);
      if(previous?.folder_id===id&&latest?.revision===previous.revision+1)state.project={...previous,folder_id:'',revision:latest.revision};
    }catch(error){toast(error.message);event.target.disabled=false;}};
  }
  async function action(name,id) {
    try{
      root()?.querySelectorAll('details[open]').forEach(item=>item.open=false);
      const item=items().find(item=>item.id===id);
      if(item?.library_kind==='conversation'){
        if(name==='open')await ctx.onOpenConversation(id);
        else if(name==='info')await showConversationInfo(id);
        else if(name==='rename')editName('重命名对话',item.name,title=>api(`/api/conversations/${encodeURIComponent(id)}/metadata`,{...json({title}),method:'PATCH'}));
        else if(name==='delete'||name==='purge')confirmConversationDelete(item,name==='purge');
        else if(name==='restore'){await api(`/api/conversations/${encodeURIComponent(id)}/restore`,{method:'POST'});await refresh();toast('对话已恢复');}
        return;
      }
      if(name==='retry')await refresh();
      else if(name==='create'){if(ui.category==='conversation')ctx.onCreateConversation();else ctx.onCreate(ui.folder);}
      else if(name==='back'){ui.folder='';draw();}
      else if(name==='folder'){ui.folder=id;ui.query='';draw();}
      else if(name==='open')await ctx.onOpen(id);
      else if(name==='info')await showInfo(id);
      else if(name==='trash'){await ctx.flushSave();ui.trash=!ui.trash;state.showTrash=ui.trash;ui.query='';await refresh();}
      else if(name==='new-folder')editName('新建文件夹','',name=>api('/api/project-folders',json({name})));
      else if(name==='rename-folder')editName('重命名文件夹',ui.folders.find(item=>item.id===id)?.name||'',name=>api(`/api/project-folders/${encodeURIComponent(id)}`,{...json({name}),method:'PATCH'}));
      else if(name==='delete-folder')deleteFolder(id);
      else if(name==='rename')editName('重命名项目',state.projects.find(item=>item.id===id)?.name||'',name=>metadata(id,{name}));
      else if(name==='move')move(id);
      else if(name==='duplicate')await ctx.onDuplicate(id);
      else if(name==='delete')ctx.onDelete(id);
      else if(name==='restore')await ctx.onRestore(id);
      else if(name==='purge')ctx.onPurge(id,ui.recycled.find(item=>item.id===id)?.name);
    }catch(error){toast(error.message);}
  }
  document.addEventListener('pointerdown',event=>{if(!event.target.closest('.projects-card-menu'))root()?.querySelectorAll('.projects-card-menu[open]').forEach(item=>item.open=false);});
  document.addEventListener('keydown',event=>{if(event.key==='Escape')root()?.querySelectorAll('.projects-card-menu[open]').forEach(item=>item.open=false);});
  return {open(){draw();refresh().catch(error=>toast(error.message));},render(projects,trashed=false){ui.trash=trashed;state.showTrash=trashed;if(trashed)ui.recycled=projects;draw();}};
}

import { createSelectionIndicator } from './home-preferences.js?v=selection-motion-10';
import { createViewportObserver, revealMedia } from './viewport-media.js?v=1';

export const BOARD_CATEGORIES=[['all','全部'],['character','人物'],['scene','场景'],['prop','道具'],['video','视频'],['audio','音频'],['image','图片'],['final','成片'],['text','文稿']];

export function storyboardItems(project,assets=[],tasks={}){
  const media=new Map(assets.map(a=>[a.id,a]));
  return (project?.nodes||[]).filter(n=>!n.mujian?.archived).map(node=>{
    const meta=node.mujian||{},asset=media.get(node.assetId),task=tasks[node.id];
    const category=meta.assetType||(['audio','audio-asset'].includes(node.type)?'audio':node.type==='timeline'?'final':
      ['video','video-asset'].includes(node.type)?'video':node.type==='character'?'character':node.type==='prop'?'prop':
      ['asset','image','image-asset'].includes(node.type)?'image':['prompt','script'].includes(node.type)?'text':null);
    if(!category)return null;
    const stopped=task?.local_stop_requested||task?.stop_requested;
    const url=asset?.url||node.assetUrl||(!stopped&&task?.status==='done'?task.video_url:'')||'';
    return {id:node.id,nodeId:node.id,category,title:node.title||asset?.name||'未命名',
      description:node.image_settings?.prompt||node.audio_settings?.prompt||node.settings?.prompt||node.script_data?.title||'',
      kind:asset?.kind||(['video','final'].includes(category)?'video':category==='audio'?'audio':'image'),url,
      ratio:node.image_settings?.aspect_ratio||node.settings?.aspect_ratio||node.aspect_ratio||'',
      duration:node.settings?.duration||asset?.duration||0,shots:meta.shots?.length||0,episode:meta.episode||0,
      index:meta.index||0,x:node.x||0,y:node.y||0,
      status:url?'已生成':stopped?'已停止':['queued','running'].includes(task?.status)?'生成中':meta.needsReview?'待核对':'待生成'};
  }).filter(Boolean).sort((a,b)=>BOARD_CATEGORIES.findIndex(c=>c[0]===a.category)-BOARD_CATEGORIES.findIndex(c=>c[0]===b.category)||a.episode-b.episode||a.index-b.index||a.y-b.y||a.x-b.x);
}

export function createViewSwitch(host,{value='storyboard',onChange,onError=()=>{}}={}){
  host.classList.add('story-view-switch');host.setAttribute('role','tablist');host.setAttribute('aria-label','故事板与画布');
  host.innerHTML='<button type="button" role="tab" data-view-mode="storyboard">故事板</button><button type="button" role="tab" data-view-mode="workflow">画布</button>';
  let current=value,busy=false;
  const marker=createSelectionIndicator(host,'[aria-selected=true]');
  function set(next){current=next;host.querySelectorAll('button').forEach(b=>{const selected=b.dataset.viewMode===next;b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1;});marker.update();}
  host.addEventListener('click',async event=>{
    const button=event.target.closest('[data-view-mode]');if(!button||busy||button.disabled)return;
    const next=button.dataset.viewMode;if(next===current)return;
    const before=current;set(next);busy=true;host.setAttribute('aria-busy','true');
    try{await onChange?.(next);}catch(error){set(before);onError(error);}finally{busy=false;host.removeAttribute('aria-busy');if(!host.getClientRects().length)set(before);}
  });
  host.addEventListener('keydown',event=>{if(['ArrowLeft','ArrowRight'].includes(event.key)){event.preventDefault();event.stopPropagation();const button=host.querySelector(`[data-view-mode="${current==='workflow'?'storyboard':'workflow'}"]`);button.focus();button.click();}});
  set(value);
  return {set,refresh:()=>marker.update(false),disableCanvas(disabled){host.querySelector('[data-view-mode=workflow]').disabled=disabled;}};
}

export function createStoryboard(host,{esc,icon,onSelect,onAction,onCanvas,viewSwitchHost,onError=()=>{}}={}){
  const previews=createViewportObserver(video=>{
    // Metadata alone can leave a blank native player. Seek once to decode a
    // cover frame without autoplaying or downloading every off-screen clip.
    video.addEventListener('loadedmetadata',()=>{
      if(video.paused&&video.currentTime===0&&Number.isFinite(video.duration)&&video.duration>0)
        video.currentTime=Math.min(.05,video.duration/2);
    },{once:true});
    revealMedia(video);
  });
  const releaseMedia=card=>card.querySelectorAll('video,audio').forEach(media=>{media.pause();media.removeAttribute('src');media.load();});
  host.classList.add('storyboard-surface');
  host.innerHTML=`<div class="storyboard-top"><div><small>STORYBOARD</small><h2></h2><p class="storyboard-summary"></p></div>${onCanvas&&!viewSwitchHost?'<div class="storyboard-switch"></div>':''}</div><div class="storyboard-toolbar"><nav class="storyboard-tabs" role="tablist" aria-label="故事板分类">${BOARD_CATEGORIES.map(([key,label])=>`<button type="button" role="tab" data-board-category="${key}" aria-selected="${key==='all'}" tabindex="${key==='all'?0:-1}">${label}<span>0</span></button>`).join('')}</nav><div class="storyboard-actions"></div></div><div class="storyboard-progress"></div><div class="board-grid" role="tabpanel" aria-label="故事板内容"></div><div class="board-empty" hidden></div>`;
  let category='all',items=[],cards=new Map(),gridStamp='',actionsStamp='';
  const tabs=host.querySelector('.storyboard-tabs'),grid=host.querySelector('.board-grid');
  const marker=createSelectionIndicator(tabs,'[aria-selected=true]');
  const viewSwitch=onCanvas?createViewSwitch(viewSwitchHost||host.querySelector('.storyboard-switch'),{onChange:next=>next==='workflow'?onCanvas():null,onError}):null;
  const glyph=item=>({character:'character',scene:'image',prop:'prop',video:'video',audio:'audio',image:'image',final:'video',text:'script'}[item.category]);
  function cardHtml(item,index){
    const media=item.url?(item.kind==='video'?`<video data-media-src="${esc(item.url)}" aria-label="${esc(item.title)}" controls preload="none" playsinline></video>`:item.kind==='audio'?`<audio src="${esc(item.url)}" controls preload="none"></audio>`:`<img src="${esc(item.url)}" alt="${esc(item.title)}" loading="lazy">`):icon(glyph(item));
    const detail=[item.episode?'第 '+item.episode+' 集':'',item.ratio,item.duration?item.duration+'s':'',item.shots?item.shots+' 个镜头':''].filter(Boolean).join(' · ');
    return `<div class="board-media">${media}<span class="storyboard-status">${esc(item.status||'待生成')}</span></div><button type="button" data-board-node="${esc(item.nodeId||'')}" ${item.nodeId?'':'disabled'}><span class="board-number">${String(index+1).padStart(2,'0')}</span><span><b>${esc(item.title)}</b><small>${esc(detail)}</small></span>${item.nodeId?icon('arrowUpRight'):''}</button><p>${esc(item.description.slice(0,120))}</p>`;
  }
  function draw(){
    for(const b of tabs.querySelectorAll('button')){
      const key=b.dataset.boardCategory,selected=key===category;
      b.setAttribute('aria-selected',String(selected));b.tabIndex=selected?0:-1;
      b.querySelector('span').textContent=String(key==='all'?items.length:items.filter(n=>n.category===key).length);
    }
    const visible=items.filter(n=>category==='all'||n.category===category),stamp=JSON.stringify([category,visible]);
    if(stamp!==gridStamp){
      gridStamp=stamp;const next=new Map();
      visible.forEach((item,index)=>{
        const signature=JSON.stringify([item,index]),previous=cards.get(item.id);
        let card=previous?.element;
        if(!card){card=document.createElement('article');card.className='board-card';card.dataset.storyboardId=item.id;}
        if(previous?.signature!==signature){releaseMedia(card);card.innerHTML=cardHtml(item,index);}
        if(grid.children[index]!==card)grid.insertBefore(card,grid.children[index]||null);
        next.set(item.id,{element:card,signature});
      });
      for(const [id,old] of cards)if(!next.has(id)){releaseMedia(old.element);old.element.remove();}
      cards=next;
      previews.observe(grid.querySelectorAll('video[data-media-src]'));
    }
    const empty=host.querySelector('.board-empty');empty.hidden=visible.length>0;
    empty.textContent=items.length?'此分类还没有内容。':'人物、场景、道具和视频将在这里逐项呈现。';
    marker.update();viewSwitch?.set('storyboard');
  }
  tabs.addEventListener('click',event=>{const b=event.target.closest('[data-board-category]');if(b){category=b.dataset.boardCategory;draw();}});
  tabs.addEventListener('keydown',event=>{if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;event.preventDefault();event.stopPropagation();const keys=BOARD_CATEGORIES.map(c=>c[0]);category=event.key==='Home'?keys[0]:event.key==='End'?keys.at(-1):keys[(keys.indexOf(category)+(event.key==='ArrowRight'?1:keys.length-1))%keys.length];draw();tabs.querySelector('[aria-selected=true]').focus();});
  host.addEventListener('click',event=>{const node=event.target.closest('[data-board-node]'),action=event.target.closest('[data-board-action]');if(node&&!node.disabled)Promise.resolve(onSelect?.(node.dataset.boardNode)).catch(onError);if(action&&!action.disabled)Promise.resolve(onAction?.(action.dataset.boardAction,action)).catch(onError);});
  return {update({title,items:nextItems,actions=[],progress='',loading=false,hasCanvas=true}){
    items=nextItems||[];host.querySelector('.storyboard-top h2').textContent=title||'故事板';
    host.querySelector('.storyboard-summary').textContent=loading?'正在载入故事板…':`${items.length} 项内容 · 按分类浏览，点击进入节点编辑`;
    const stamp=JSON.stringify(actions);if(stamp!==actionsStamp){actionsStamp=stamp;host.querySelector('.storyboard-actions').innerHTML=actions.map(a=>`<button type="button" data-board-action="${esc(a.id)}" ${a.disabled?'disabled':''} class="${a.primary?'primary':''}">${esc(a.label)}</button>`).join('');}
    const p=host.querySelector('.storyboard-progress');if(p.innerHTML!==progress)p.innerHTML=progress;
    viewSwitch?.disableCanvas(!hasCanvas);draw();
  },refresh(){marker.update(false);viewSwitch?.refresh();},selectCategory(value){if(BOARD_CATEGORIES.some(c=>c[0]===value)){category=value;draw();}},pause(){host.querySelectorAll('video,audio').forEach(m=>m.pause());}};
}

import { installOverlayScrollbar } from './overlay-scrollbar.js?v=1';
import { createViewportObserver, revealMedia } from './viewport-media.js?v=1';
/** One home, two independent creation methods. No generation is started here. */
export function createHomeHub({state,composer,chat,icon,escapeHtml:esc,onCreateCanvas,onOpenCanvas,onProjects,toast}) {
  const root=document.querySelector('#agentView'),welcome=root.querySelector('.agent-welcome');
  let stage='generate',landing=true,creating=false;
  const covers=createViewportObserver(revealMedia);
  try{if(sessionStorage.getItem('jht.home.method')==='canvas')stage='canvas';}catch{}
  root.classList.add('home-landing');root.dataset.homeStage=stage;
  installOverlayScrollbar(root, { floating: true, enabled: () => root.classList.contains('home-landing') && root.classList.contains('active-view') && !root.classList.contains('agent-docked') });
  welcome.innerHTML=`<h1>你好，和建绘通聊聊你的灵感</h1><div class="home-method-switch" role="tablist" aria-label="创作方式"><span class="home-method-thumb" aria-hidden="true"></span><button type="button" id="homeGenerateMethod" role="tab" aria-selected="true" aria-controls="homeGenerationArea" data-home-method="generate">生成</button><button type="button" id="homeCanvasMethod" role="tab" aria-selected="false" aria-controls="homeCanvasArea" data-home-method="canvas" tabindex="-1">画布</button></div>`;
  root.querySelector('.agent-composer-wrap').id='homeGenerationArea';
  const referenceMarkup=`${icon('plus')}<span>参考素材</span>`;
  const mediaReference=document.createElement('button');mediaReference.type='button';mediaReference.className='home-reference-entry';mediaReference.setAttribute('aria-label','添加参考素材');mediaReference.innerHTML=referenceMarkup;
  root.querySelector('#homeMediaComposer').prepend(mediaReference);mediaReference.onclick=()=>root.querySelector('#homeMediaUpload').click();
  const agentReference=mediaReference.cloneNode(true);agentReference.dataset.command='upload';root.querySelector('#agentComposer').prepend(agentReference);
  const area=document.createElement('section');area.id='homeCanvasArea';area.className='home-canvas-area';area.hidden=true;area.setAttribute('aria-label','画布创作');
  area.innerHTML=`<div class="home-canvas-launchers"><button type="button" class="home-new-canvas" data-canvas-start="blank"><span>${icon('plus')}</span><strong>新建画布</strong></button><button type="button" class="home-canvas-preset video" data-canvas-start="video"><span class="home-preset-kicker">自由创作</span><span class="home-preset-art">${icon('play')}<i></i><i></i></span><strong>视频创作</strong>${icon('arrowUpRight')}</button><button type="button" class="home-canvas-preset image" data-canvas-start="image"><span class="home-preset-kicker">自由创作</span><span class="home-preset-art">${icon('imageNode')}<i></i><i></i></span><strong>图像创作</strong>${icon('arrowUpRight')}</button></div><section class="home-canvas-recents" aria-label="最近画布"></section>`;
  root.querySelector('.agent-composer-wrap').after(area);
  const discover=document.createElement('section');discover.className='home-discover';
  discover.innerHTML=`<header><h2>开启你的创作</h2><span>从灵感出发，让创意发生</span></header><div class="home-discover-grid">${[['image','光影之间，灵感成像','图片生成','imageNode'],['video','让每一个想法，动起来','视频生成','play'],['audio','为你的故事，注入声音','音频生成','audio']].map(([kind,title,label,glyph])=>`<button type="button" class="home-discover-card ${kind}" data-discover="${kind}"><span class="home-discover-art">${icon(glyph)}<i></i><i></i></span><small>${label}</small><strong>${title}</strong><span class="home-discover-arrow">${icon('arrowUpRight')}</span></button>`).join('')}</div>`;
  root.append(discover);
  function sync(){
    root.dataset.homeStage=stage;root.classList.toggle('home-landing',landing);
    document.body.classList.toggle('home-landing-active',state.view==='agent'&&landing&&!root.classList.contains('agent-docked'));
    area.hidden=stage!=='canvas'||!landing;
    welcome.querySelectorAll('[data-home-method]').forEach(button=>{const selected=button.dataset.homeMethod===stage;button.setAttribute('aria-selected',String(selected));button.tabIndex=selected?0:-1;});
    discover.hidden=!landing;
  }
  function setStage(next){
    if(!['generate','canvas'].includes(next))return;stage=next;landing=true;
    try{sessionStorage.setItem('jht.home.method',stage);}catch{}
    root.querySelectorAll('video,audio').forEach(el=>el.pause());sync();renderCanvases();
    const url=new URL(location.href);url.search='';url.searchParams.set('view','home');if(stage==='canvas')url.searchParams.set('mode','canvas');history.replaceState(null,'',url);
  }
  welcome.querySelector('.home-method-switch').onclick=e=>{const button=e.target.closest('[data-home-method]');if(button)setStage(button.dataset.homeMethod);};
  welcome.querySelector('.home-method-switch').onkeydown=e=>{if(['ArrowLeft','ArrowRight','Home','End'].includes(e.key)){e.preventDefault();setStage(e.key==='Home'?'generate':e.key==='End'?'canvas':stage==='generate'?'canvas':'generate');welcome.querySelector('[aria-selected=true]').focus();}};
  async function create(kind,prompt=''){
    if(creating)return;creating=true;area.classList.add('is-creating');area.querySelectorAll('button').forEach(b=>b.disabled=true);
    try{await onCreateCanvas({kind,prompt});}catch(error){toast(error.message);}finally{creating=false;area.classList.remove('is-creating');area.querySelectorAll('button').forEach(b=>b.disabled=false);}
  }
  area.querySelector('.home-canvas-launchers').onclick=e=>{const button=e.target.closest('[data-canvas-start]');if(button)create(button.dataset.canvasStart);};
  function renderCanvases(){
    const host=area.querySelector('.home-canvas-recents');host.innerHTML=`<header><h2>最近画布</h2><button type="button" data-all-canvases>查看全部 ${icon('arrowUpRight')}</button></header>${state.projects.length?`<div class="home-canvas-grid">${state.projects.slice(0,6).map(project=>{
      const asset=(project.nodes||[]).map(node=>state.assets.find(a=>a.id===node.assetId&&['image','prop'].includes(a.kind))).find(Boolean);
      return `<button type="button" data-open-canvas="${esc(project.id)}"><span class="home-canvas-cover">${asset?`<img data-media-src="${esc(asset.url)}" alt="" decoding="async">`:icon('grid')}</span><strong>${esc(project.name)}</strong><small>${new Date((project.updated_at||project.created_at)*1000).toLocaleDateString('zh-CN')}</small></button>`;
    }).join('')}</div>`:'<div class="home-canvas-empty">还没有画布，从上方开始你的第一次自由创作。</div>'}`;
    covers.observe(host.querySelectorAll('[data-media-src]'));
    host.onclick=e=>{const button=e.target.closest('[data-open-canvas]');if(button)onOpenCanvas(button.dataset.openCanvas);if(e.target.closest('[data-all-canvases]'))onProjects();};
  }
  discover.onclick=e=>{const button=e.target.closest('[data-discover]');if(!button)return;setStage('generate');composer.select(button.dataset.discover,true);root.querySelector('.agent-composer-wrap').scrollIntoView({behavior:matchMedia('(prefers-reduced-motion:reduce)').matches?'instant':'smooth',block:'center'});};
  document.addEventListener('submit',e=>{
    if(root.classList.contains('agent-docked')||!root.contains(e.target)||!['agentComposer','homeMediaComposer'].includes(e.target.id))return;
    const input=e.target.querySelector('textarea');if(input?.value.trim()){landing=false;stage='generate';sync();}
  },true);
  document.addEventListener('click',e=>{if(!root.classList.contains('agent-docked')&&root.contains(e.target)&&e.target.closest('[data-command=new]')){landing=true;stage='generate';sync();}},true);
  new MutationObserver(sync).observe(root,{attributes:true,attributeFilter:['class']});
  renderCanvases();sync();
  return {
    async goHome(mode){const preserveDraft=!root.dataset.conversationId&&!root.classList.contains('has-media-conversation');landing=true;if(mode)stage=mode;composer.resetHome();sync();await chat.newConversation(preserveDraft);renderCanvases();setStage(stage);},
    showConversation(){landing=false;stage='generate';sync();},
    onView(){sync();if(state.view==='agent')renderCanvases();},
    setStage
  };
}

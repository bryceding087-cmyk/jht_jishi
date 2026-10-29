import { icon } from './iconography.js?v=library-toggle-1';
import { loadingMarkup } from './loading-state.js?v=1';
import { createViewportObserver, revealMedia } from './viewport-media.js?v=1';
import { marketMediaUrl, withMarketSession, marketResponseData } from './market-session.js?v=market-sdk-3';

const TYPES = { image: ['图片', 'image'], video: ['视频', 'video'], audio: ['音频', 'audio'] };
const ACCEPT = { image: 'image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp', prop: 'image/jpeg,image/png,image/webp,.jpg,.jpeg,.png,.webp', video: 'video/mp4,video/webm,.mp4,.webm', audio: 'audio/mpeg,audio/wav,audio/mp4,audio/ogg,.mp3,.wav,.m4a,.ogg', document: '.txt,.xlsx', text: '.txt', script: '.xlsx' };
const LIMITS = { image: 'JPG、PNG、WebP · 单张 20 MB', prop: 'JPG、PNG、WebP · 单个 20 MB', video: 'MP4、WebM · 单个 100 MB', audio: 'MP3、WAV、M4A、OGG · 单个 30 MB', document: 'TXT 1 MB / 分镜 Excel 5 MB', text: 'TXT · 单个 1 MB', script: '分镜 Excel .xlsx · 单个 5 MB' };
const kindOf = asset => asset.kind === 'prop' ? 'image' : asset.kind || 'image';
const isDocument = asset => ['text', 'script'].includes(kindOf(asset));
const sourceOf = asset => ['generated', 'generation', 'ai'].includes(asset.source) || asset.task_id ? 'generated' : asset.source === 'saved' ? 'saved' : 'upload';
const bytes = number => !number ? '' : number < 1024 * 1024 ? `${Math.round(number / 1024)} KB` : `${(number / 1024 / 1024).toFixed(1)} MB`;

export function createAssetLibrary(ctx) {
  const { state, api, json, escapeHtml: esc, toast, showModal, closeModal } = ctx;
  const ui = { collection: 'history', pickerCollection: 'history', type: 'all', query: '', source: '', scope: '', sort: 'newest', folder: '*', selected: new Set(), folders: [], loading: false, loaded: false, error: '', jobs: [], uploading: false };
  let panelMode = '', pickerOptions = null, panelQuery = '', refreshSequence = 0, refreshTimer = null;
  const $ = (selector, root = document) => root.querySelector(selector);
  const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];
  const tagsOf = asset => Array.isArray(asset.tags) ? asset.tags : [];
  const byId = id => state.assets.find(asset => asset.id === id);
  const matchesType = (asset, type = ui.type) => !!TYPES[kindOf(asset)] && (type === 'all' || kindOf(asset) === type);
  const safeUrl = value => /^(?:https?:\/\/|\/[^/])/.test(String(value || '')) ? esc(value) : '';
  const action = (name, label, extra = '') => `<button type="button" class="al-btn${extra.includes('al-primary') ? ' al-primary' : ''}" data-al-action="${name}" ${extra.replace(/class="[^"]*"/g, '')}>${label}</button>`;
  const empty = (title, detail, button = '') => `<div class="al-empty"><span>${icon(TYPES[ui.type]?.[1] || 'image')}</span><h3>${esc(title)}</h3><p>${esc(detail)}</p>${button}</div>`;
  const redraw = () => { if (state.view === 'assets') render(); if (panelMode === 'picker' && $('#alPicker')) renderPicker();  };
  const changed = () => { ctx.onChanged?.(); redraw(); };
  const patch = body => ({ ...json(body), method: 'PATCH' });

  async function refresh() {
    clearTimeout(refreshTimer);
    const sequence=++refreshSequence;
    ui.loading=true;ui.error='';redraw();
    const options = { background: state.view === 'assets' };
    const results = await Promise.allSettled([api('/api/assets', options), api('/api/asset-folders', options), api('/api/characters', options)]);
    if(sequence!==refreshSequence)return false;
    if (results[0].status === 'fulfilled') state.assets = results[0].value;
    if (results[1].status === 'fulfilled') ui.folders = results[1].value;
    if (results[2].status === 'fulfilled') state.characters = results[2].value;
    ui.error = results.filter(item => item.status === 'rejected').map(item => item.reason?.message || '读取失败').join('；');
    ui.loaded = ui.loaded || results[0].status === 'fulfilled';
    ui.loading = false;
    const ids = new Set(state.assets.map(item => item.id));
    ui.selected = new Set([...ui.selected].filter(id => ids.has(id)));
    if (ui.folder !== '*' && ui.folder && !ui.folders.some(folder => folder.id === ui.folder)) ui.folder = '*';
    changed();
    if(!ui.error&&state.assets.some(asset=>asset.kind==='video'&&asset.file_status==='pending')){
      refreshTimer=setTimeout(()=>{if(state.view==='assets'||panelMode==='picker'&&$('#alPicker'))refresh();},3000);
    }
    return !ui.error;
  }

  function filteredAssets() {
    return state.assets.filter(asset => matchesType(asset) && (ui.collection === 'history' || asset.in_library))
      .sort((a,b) => (ui.sort === 'oldest' ? 1 : -1) * ((a.created_at || 0) - (b.created_at || 0)) || String(a.id).localeCompare(String(b.id)));
  }
  function thumbnail(asset, full = false) {
    const url = safeUrl(asset.url), kind = kindOf(asset);
    if (kind === 'image' || kind === 'prop') return url ? `<img src="${url}" alt="${esc(asset.name)}" loading="lazy">` : `<span class="al-file-symbol">${icon(kind === 'prop' ? 'prop' : 'image')}</span>`;
    if (kind === 'video') return url ? `<video src="${url}" ${full ? 'controls playsinline' : 'preload="metadata" muted playsinline'}></video>${full ? '' : `<span class="al-play">${icon('play')}</span>`}` : `<span class="al-file-symbol">${icon('video')}</span>`;
    if (kind === 'audio') return `<div class="al-audio-art"><span>${icon('audio')}</span><i></i><i></i><i></i><i></i><i></i><i></i><i></i></div>${full && url ? `<audio controls src="${url}" preload="metadata"></audio>` : ''}`;
    if (kind === 'script') return `<div class="al-document-art"><span>${icon('script')} 分镜脚本</span><strong>${asset.script_data?.scenes?.length || 0}<small> 个镜头</small></strong><p>${esc(asset.script_data?.style || asset.description || '结构化分镜 · 可编辑复用')}</p></div>`;
    return `<div class="al-document-art"><span>${icon('text')} 文本素材</span><p>${esc(String(asset.content || asset.description || '文本内容').slice(0, full ? 100000 : 180))}</p></div>`;
  }
  function folderOptions(current = '', all = false) {
    return `${all ? '<option value="*">所有文件夹</option>' : ''}<option value="" ${current === '' ? 'selected' : ''}>未分组</option>${ui.folders.map(folder => `<option value="${esc(folder.id)}" ${folder.id === current ? 'selected' : ''}>${esc(folder.name)}</option>`).join('')}`;
  }
  const moreIcon = '<svg class="al-more-icon" viewBox="0 0 24 24" aria-hidden="true"><circle cx="12" cy="5.5" r="1.7" fill="currentColor"/><circle cx="12" cy="12" r="1.7" fill="currentColor"/><circle cx="12" cy="18.5" r="1.7" fill="currentColor"/></svg>';
  function typeLabel(kind) { return TYPES[kind]?.[0] || (kind === 'script' ? '脚本' : '文本'); }
  function typeIcon(kind) { return TYPES[kind]?.[1] || (kind === 'script' ? 'script' : 'text'); }
  function closeAssetMenus() {
    document.querySelectorAll('.al-card.is-menu-open').forEach(card => {
      card.classList.remove('is-menu-open');
      const menu = card.querySelector('.al-card-menu');
      const button = card.querySelector('.al-card-more');
      if (menu) { menu.hidden = true; menu.classList.remove('is-flip', 'is-up'); }
      if (button) button.setAttribute('aria-expanded', 'false');
    });
  }
  function assetCard(asset, picker = false) {
    const kind = kindOf(asset), id = esc(asset.id), label = typeLabel(kind);
    const menu = picker ? '' : `<div class="al-card-menu" role="menu" hidden><button type="button" role="menuitem" data-al-delete="${id}">删除</button><button type="button" role="menuitem" data-al-download="${id}">下载</button><button type="button" role="menuitem" data-al-move="${id}">移动</button><button type="button" role="menuitem" data-al-rename="${id}">重命名</button></div>`;
    const more = picker ? '' : `<button type="button" class="al-card-more" data-al-menu="${id}" aria-label="更多操作" aria-haspopup="menu" aria-expanded="false">${moreIcon}</button>`;
    return `<article class="al-card ${ui.selected.has(asset.id) && !picker ? 'is-selected' : ''}" data-asset-id="${id}"><div class="al-card-preview">${thumbnail(asset)}<div class="al-card-shade"></div><div class="al-card-hover"><button type="button" data-al-preview="${id}">查看</button><button type="button" data-al-add="${id}">${picker ? '选择' : '添加到画布'}</button></div>${more}</div>${menu}<div class="al-card-foot"><h3 title="${esc(asset.name)}">${esc(asset.name)}</h3><span class="al-card-type">${icon(typeIcon(kind), 'ui-icon-sm')}${esc(label)}</span></div></article>`;
  }
  function uploadJobs() {
    if (!ui.jobs.length) return '';
    return `<div class="al-upload-jobs" role="status"><div><strong>${ui.uploading ? '正在上传素材' : '上传结果'}</strong>${!ui.uploading ? action('clear-jobs', '收起') : '<small>请保留页面，等待上传完成</small>'}</div>${ui.jobs.map(job => `<div class="al-upload-job ${job.status}"><span title="${esc(job.name)}">${esc(job.name)}</span><progress max="100" value="${job.progress || 0}"></progress><small>${esc(job.status === 'done' ? '已加入生成历史' : job.status === 'error' ? job.error : job.status === 'pending' ? '等待上传' : `${job.progress || 0}%`)}</small></div>`).join('')}</div>`;
  }
  const mediaDurations = new Map();
  let activePreview = null;
  function stopPreviews() {
    const preview=activePreview;activePreview=null;
    if(!preview)return;
    preview.video.pause();
    try{preview.video.currentTime=0;}catch{}
  }
  async function startPreview(card, video) {
    if(activePreview?.video===video)return;
    stopPreviews();
    revealMedia(card);
    const preview={card,video};activePreview=preview;
    video.muted=true;video.defaultMuted=true;video.loop=true;
    try{
      await video.play();
      if(activePreview!==preview&&activePreview?.video!==video){video.pause();try{video.currentTime=0;}catch{}}
    }catch{
      if(activePreview===preview)activePreview=null;
    }
  }
  const previewObserver = new IntersectionObserver(entries=>{
    for(const entry of entries)if(!entry.isIntersecting&&activePreview?.card===entry.target)stopPreviews();
  });
  function bindPreviews(host) {
    previewObserver.disconnect();
    host.querySelectorAll('.al-history-card').forEach(card=>{
      const video=card.querySelector('video');if(!video)return;
      previewObserver.observe(card);
      card.addEventListener('pointerenter',event=>{if(event.pointerType!=='touch')startPreview(card,video);});
      card.addEventListener('pointerleave',()=>{if(activePreview?.video===video)stopPreviews();});
      video.addEventListener('error',()=>{if(activePreview?.video===video)stopPreviews();});
    });
  }
  document.addEventListener('visibilitychange',()=>{if(document.hidden)stopPreviews();});
  window.addEventListener('blur',stopPreviews);
  window.addEventListener('pagehide',stopPreviews);
  const calendarDate = value => {
    const day = new Date(Number(value || 0) * 1000);
    return value ? `${day.getFullYear()}-${String(day.getMonth()+1).padStart(2,'0')}-${String(day.getDate()).padStart(2,'0')}` : '日期未知';
  };
  const musicNote = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M15 4v12M15 4h5"/><ellipse cx="10.5" cy="16.5" rx="4.5" ry="4.5"/></svg>';
  function historyCard(asset) {
    const id=esc(asset.id),kind=kindOf(asset),duration=Number(asset.duration || mediaDurations.get(asset.url) || 0);
    return `<article class="al-history-card ${ui.selected.has(asset.id)?'is-selected':''}" data-asset-id="${id}" title="${esc(asset.name)}">
      <button type="button" class="al-history-preview" data-al-preview="${id}" aria-label="预览 ${esc(asset.name)}">${kind==='audio'?`<span class="al-history-note">${musicNote}</span>`:kind==='video'?`<video data-media-src="${safeUrl(asset.url)}" preload="none" muted loop playsinline></video>`:`<img data-media-src="${safeUrl(asset.url)}" alt="${esc(asset.name)}" decoding="async">`}</button>
      <input class="al-history-check" type="checkbox" data-al-select="${id}" aria-label="选择 ${esc(asset.name)}" ${ui.selected.has(asset.id)?'checked':''}>
      ${kind==='audio'||kind==='video'?`<span class="al-history-duration" data-duration-for="${id}" ${duration?'':'hidden'}>${duration.toFixed(1)}s</span>`:''}
      ${asset.file_status==='failed'?'<span class="al-history-file-error">文件下载失败 · 可重试</span>':''}
      <div class="al-history-actions"><button type="button" data-al-save="${id}" ${saving.has(asset.id)?'disabled':''} aria-label="${asset.in_library?'移出资产库':'保存至资产库'}" title="${asset.in_library?'移出资产库':'保存至资产库'}">${icon(asset.in_library?'libraryRemove':'library')}</button><button type="button" data-al-download="${id}" aria-label="下载 ${esc(asset.name)}" title="下载">${icon('download')}</button><button type="button" data-al-delete="${id}" aria-label="删除 ${esc(asset.name)}" title="删除">${icon('trash')}</button></div>
    </article>`;
  }
  function readDurations(host) {
    host.querySelectorAll('[data-duration-for]').forEach(label=>{
      const asset=byId(label.dataset.durationFor);if(!asset?.url||asset.duration||mediaDurations.has(asset.url))return;
      const media=kindOf(asset)==='video'?label.closest('article').querySelector('video'):new Audio();
      media.preload='metadata';
      const finish=()=>{
        const duration=Number.isFinite(media.duration)?media.duration:0;
        if(duration){mediaDurations.set(asset.url,duration);if(label.isConnected){label.hidden=false;label.textContent=duration.toFixed(1)+'s';}}
        if(kindOf(asset)==='audio'){media.removeAttribute('src');media.load();}
      };
      if(media.readyState>=1)finish();else {media.addEventListener('loadedmetadata',finish,{once:true});if(kindOf(asset)==='audio')media.src=asset.url;}
    });
  }
  const historyMedia = createViewportObserver(card => { revealMedia(card); readDurations(card); });
  function moveTabSlider(group) {
    const selected=group.querySelector('[aria-selected="true"]');
    if(!selected?.offsetWidth)return;
    let slider=group.querySelector('.al-tab-slider');
    const fresh=!slider;
    if(fresh){slider=document.createElement('span');slider.className='al-tab-slider';slider.setAttribute('aria-hidden','true');}
    slider.style.width=selected.offsetWidth+'px';slider.style.height=selected.offsetHeight+'px';
    slider.style.transform=`translate(${selected.offsetLeft}px,${selected.offsetTop}px)`;
    if(fresh){group.prepend(slider);group.classList.add('has-tab-slider');}
  }
  const tabResize=new ResizeObserver(entries=>{
    for(const entry of entries){const group=entry.target.closest('.al-collection-tabs,.al-history-tabs');if(group?.isConnected)moveTabSlider(group);}
  });
  function render() {
    const host=$('#libraryGrid');if(!host)return;
    stopPreviews();
    host.classList.add('asset-library-host');
    const assets=filteredAssets(),groups=new Map();
    for(const asset of assets){const date=calendarDate(asset.created_at);if(!groups.has(date))groups.set(date,[]);groups.get(date).push(asset);}
    const pool=state.assets.filter(asset=>matchesType(asset,'all')&&(ui.collection==='history'||asset.in_library));
    // Keep tabs mounted so their slider transitions and keyboard focus survive filtering.
    if(!$('#alLibrary',host)){
      tabResize.disconnect();
      host.innerHTML=`<div id="alLibrary" class="al-library al-history">
      <div class="al-history-heading"><div class="al-collection-tabs" role="tablist" aria-label="内容范围">${[['history','生成历史'],['library','资产库']].map(([value,label])=>`<button type="button" role="tab" data-al-collection="${value}" aria-selected="${ui.collection===value}">${label}</button>`).join('')}</div><div class="al-history-filters"><button type="button" data-al-action="upload">${icon('upload')}导入</button><button type="button" id="alHistorySort" aria-label="${ui.sort==='newest'?'当前最新优先，切换最早优先':'当前最早优先，切换最新优先'}" title="${ui.sort==='newest'?'最新优先':'最早优先'}">${icon('sort')}<span class="sr-only">切换排序</span></button></div></div>
      <p class="al-collection-note"></p><div class="al-history-jobs"></div>
      <div class="al-history-tabs" role="tablist" aria-label="素材分类">${[['all','全部'],...Object.entries(TYPES).map(([key,value])=>[key,value[0]])].map(([type,label])=>`<button type="button" role="tab" aria-selected="${ui.type===type}" data-al-tab="${type}">${label}<small>${pool.filter(asset=>matchesType(asset,type)).length}</small></button>`).join('')}</div>
      <div class="al-history-error"></div><div class="al-history-selection-slot"></div><div class="al-history-groups"></div></div>`;
      host.querySelectorAll('.al-collection-tabs,.al-history-tabs').forEach(group=>{
        tabResize.observe(group);group.querySelectorAll('button').forEach(button=>tabResize.observe(button));
      });
    }
    $$('[data-al-collection]',host).forEach(button=>button.setAttribute('aria-selected',String(button.dataset.alCollection===ui.collection)));
    $$('[data-al-tab]',host).forEach(button=>{
      button.setAttribute('aria-selected',String(button.dataset.alTab===ui.type));
      button.querySelector('small').textContent=pool.filter(asset=>matchesType(asset,button.dataset.alTab)).length;
    });
    $('.al-collection-note',host).textContent=ui.collection==='history'?'生成和导入的内容会保留在这里，点击「保存至资产库」即可收藏复用。':'这里展示你手动保存的内容，可从生成历史添加。';
    $('.al-history-jobs',host).innerHTML=uploadJobs();
    $('.al-history-error',host).innerHTML=ui.error?`<div class="al-error" role="alert">${esc(ui.error)} ${action('retry','重新读取')}</div>`:'';
    $('.al-history-selection-slot',host).innerHTML=ui.selected.size?`<div class="al-history-selection"><label><input id="alSelectAll" type="checkbox" ${assets.length&&assets.every(asset=>ui.selected.has(asset.id))?'checked':''}>全选</label><span>已选 ${ui.selected.size} 项</span><button type="button" data-al-action="download-selected">下载</button><button type="button" data-al-action="delete-selected">删除</button><button type="button" data-al-action="clear-selection">取消选择</button></div>`:'';
    $('.al-history-groups',host).innerHTML=[...groups].map(([date,list])=>`<section class="al-history-day" aria-label="${date}"><h2>${date}</h2><div class="al-history-grid">${list.map(historyCard).join('')}</div></section>`).join('')||(!ui.loading&&ui.loaded?'<div class="al-history-empty">暂无素材</div>':'');
    $('#alHistorySort',host).title=ui.sort==='newest'?'最新优先':'最早优先';
    $('#alHistorySort',host).setAttribute('aria-label',ui.sort==='newest'?'当前最新优先，切换最早优先':'当前最早优先，切换最新优先');
    host.querySelectorAll('.al-collection-tabs,.al-history-tabs').forEach(moveTabSlider);
    $('#alLibrary',host).setAttribute('aria-busy', String(ui.loading));
    bindLibrary(host);bindPreviews(host);
    historyMedia.observe(host.querySelectorAll('.al-history-card'));
    $('#alHistorySort',host).onclick=()=>{ui.sort=ui.sort==='newest'?'oldest':'newest';render();};
  }

  function placeAssetMenu(card) {
    const menu = card.querySelector('.al-card-menu');
    if (!menu) return;
    menu.classList.remove('is-flip', 'is-up');
    const rect = menu.getBoundingClientRect();
    if (rect.right > window.innerWidth - 8) menu.classList.add('is-flip');
    if (rect.bottom > window.innerHeight - 8) menu.classList.add('is-up');
  }
  function toggleAssetMenu(button) {
    const card = button.closest('.al-card');
    const menu = card?.querySelector('.al-card-menu');
    if (!card || !menu) return;
    const willOpen = menu.hidden;
    closeAssetMenus();
    if (!willOpen) return;
    menu.hidden = false;
    card.classList.add('is-menu-open');
    button.setAttribute('aria-expanded', 'true');
    placeAssetMenu(card);
  }
  function bindCards(root, onPick) {
    $$('[data-al-save]', root).forEach(button => button.onclick = async event => { event.stopPropagation();button.disabled=true;await setLibraryMembership(button.dataset.alSave);if(button.isConnected)button.disabled=false; });
    $$('[data-al-preview]', root).forEach(element => { element.onclick = event => { event.stopPropagation(); preview(byId(element.dataset.alPreview), onPick); }; });
    $$('[data-al-add]', root).forEach(button => button.onclick = event => { event.stopPropagation(); closeAssetMenus(); const asset = byId(button.dataset.alAdd); if (!asset) return; if (onPick) onPick(asset); else if (!state.project) toast('请先创建或打开一个项目'); else ctx.onAddAsset(asset); });
    $$('[data-al-menu]', root).forEach(button => button.onclick = event => { event.stopPropagation(); toggleAssetMenu(button); });
    $$('[data-al-delete]', root).forEach(button => button.onclick = event => { event.stopPropagation(); closeAssetMenus(); deleteAssets([button.dataset.alDelete]); });
    $$('[data-al-download]', root).forEach(button => button.onclick = event => { event.stopPropagation(); closeAssetMenus(); downloadAsset(byId(button.dataset.alDownload), button); });
    $$('[data-al-move]', root).forEach(button => button.onclick = event => { event.stopPropagation(); closeAssetMenus(); moveAsset(byId(button.dataset.alMove)); });
    $$('[data-al-rename]', root).forEach(button => button.onclick = event => { event.stopPropagation(); closeAssetMenus(); renameAsset(byId(button.dataset.alRename)); });
  }
  function bindLibrary(root) {
    bindCards(root);
    $$('[data-al-collection]',root).forEach(button=>button.onclick=()=>{ui.collection=button.dataset.alCollection;ui.selected.clear();render();});
    $$('[data-al-tab]', root).forEach(button => button.onclick = () => { ui.type = button.dataset.alTab; ui.selected.clear(); ui.query = ''; render(); });
    $$('[data-al-folder]', root).forEach(button => button.onclick = () => { ui.folder = button.dataset.alFolder; ui.selected.clear(); render(); });
    $$('[data-al-select]', root).forEach(input => input.onchange = () => { input.checked ? ui.selected.add(input.dataset.alSelect) : ui.selected.delete(input.dataset.alSelect); render(); });
    if ($('#alSearch', root)) $('#alSearch', root).oninput = event => { ui.query = event.target.value; render(); };
    [['#alSource', 'source'], ['#alScope', 'scope'], ['#alSort', 'sort']].forEach(([selector, key]) => { const input = $(selector, root); if (input) input.onchange = () => { ui[key] = input.value; render(); }; });
    if ($('#alSelectAll', root)) $('#alSelectAll', root).onchange = event => { filteredAssets().forEach(item => event.target.checked ? ui.selected.add(item.id) : ui.selected.delete(item.id)); render(); };
    const actions = { upload: () => upload(ui.type==='all'?['image','video','audio']:ui.type), 'download-selected': () => [...ui.selected].forEach(id=>downloadAsset(byId(id))), 'generate-image': () => generateImage(), 'new-folder': () => editFolder(), 'rename-folder': () => editFolder(ui.folders.find(folder => folder.id === ui.folder)), 'delete-folder': () => deleteFolder(), 'new-character': () => editCharacter(), 'clear-filters': () => { Object.assign(ui, { query: '', source: '', scope: '', folder: '*' }); render(); }, 'clear-selection': () => { ui.selected.clear(); render(); }, 'move-selected': () => moveSelected(), 'delete-selected': () => deleteAssets([...ui.selected]), 'clear-jobs': () => { ui.jobs = []; render(); }, retry: () => refresh() };
    $$('[data-al-action]', root).forEach(button => button.onclick = () => actions[button.dataset.alAction]?.());
  }

  function modal(content) { showModal(content, 'asset-modal'); }
  function formError(message) { const element = $('#alFormError'); if (element) { element.hidden = false; element.textContent = message; } else toast(message); }
  function metadataFields(asset = {}) {
    return `<label>素材名称<input id="alName" required maxlength="160" value="${esc(asset.name || '')}" placeholder="为素材起一个容易查找的名字"></label><div class="al-form-pair"><label>文件夹<select id="alFolder">${folderOptions(asset.folder_id || (ui.folder === '*' ? '' : ui.folder))}</select></label><label>标签<input id="alTags" maxlength="300" value="${esc(tagsOf(asset).join('，'))}" placeholder="用逗号分隔，如：工地，航拍"></label></div><label>素材说明<textarea id="alDescription" rows="3" maxlength="2000" placeholder="记录用途、场景或拍摄信息">${esc(asset.description || '')}</textarea></label>`;
  }
  function metadataValues() { return { name: $('#alName').value.trim(), folder_id: $('#alFolder').value, tags: [...new Set($('#alTags').value.split(/[,，、;；\n]/).map(value => value.trim()).filter(Boolean))], description: $('#alDescription').value.trim() }; }
  const downloading = new Set();
  async function downloadAsset(asset, button) {
    if (!asset) return;
    const url = String(asset.url || '');
    const saveBlob = (blob, filename) => {
      const objectUrl = URL.createObjectURL(blob);
      const link = document.createElement('a');
      link.href = objectUrl; link.download = filename;
      document.body.append(link); link.click(); link.remove();
      window.setTimeout(() => URL.revokeObjectURL(objectUrl), 60000);
    };
    if (/^(?:https?:\/\/|\/)/.test(url)) {
      if (!asset.id || downloading.has(asset.id)) return;
      const id = asset.id;
      downloading.add(id);
      if (button) button.disabled = true;
      toast('正在准备下载…');
      try {
        asset = await api(`/api/assets/${encodeURIComponent(id)}`, {background:true});
        if (kindOf(asset)==='video' && asset.task_id && (asset.file_status!=='cached' || !asset.url?.startsWith('/uploads/'))) {
          asset = rememberAsset(await api(`/api/assets/${encodeURIComponent(id)}/retry-file`, {...json({}),background:true}));
          if (asset.file_status!=='cached') throw new Error('服务器未能保存视频文件。'+(asset.file_error||'请稍后重试。'));
        }
        const blob = await api(`/api/assets/${encodeURIComponent(id)}/download`, {responseType:'blob',background:true,redirect:'error'});
        if (!blob.size) throw new Error('服务器返回了空文件，请重试。');
        const suffix = (String(asset.url||'').split('?')[0].match(/\.[a-z0-9]{1,8}$/i) || [''])[0];
        const name = asset.name || '素材';
        saveBlob(blob, suffix && !name.toLowerCase().endsWith(suffix.toLowerCase()) ? `${name}${suffix}` : name);
        toast('已开始下载');
      } catch (error) { toast(error.message || '文件下载失败，请重试'); }
      finally { downloading.delete(id); if (button?.isConnected) button.disabled = false; }
      return;
    }
    const kind = kindOf(asset);
    if (kind === 'text' && asset.content) return saveBlob(new Blob([asset.content], { type: 'text/plain;charset=utf-8' }), `${asset.name || '文本素材'}.txt`);
    if (kind === 'script') {
      const scenes = asset.script_data?.scenes || [];
      if (!scenes.length) return toast('这个脚本没有可下载的内容');
      const text = [`${asset.name || '分镜脚本'}`, ...(asset.script_data?.style ? [`风格：${asset.script_data.style}`] : []), '', ...scenes.map((scene, index) => [`第 ${scene.index || index + 1} 镜`, scene.duration ? `时长：${scene.duration}` : '', scene.description || '', scene.final_prompt ? `提示词：${scene.final_prompt}` : ''].filter(Boolean).join('\n'))].join('\n');
      return saveBlob(new Blob([text], { type: 'text/plain;charset=utf-8' }), `${asset.name || '分镜脚本'}.txt`);
    }
    toast('这个素材没有可下载的文件');
  }
  function moveAsset(asset) {
    if (!asset) return;
    modal(`<div class="al-modal-kicker">素材管理</div><h2>移动「${esc(asset.name)}」</h2><form id="alMoveForm" class="al-form"><label>目标文件夹<select id="alMoveFolder">${folderOptions(asset.folder_id || '')}</select></label><p id="alFormError" class="al-error" role="alert" hidden></p><div class="al-modal-actions"><span></span><button type="button" class="al-btn" id="alMoveCancel">取消</button><button type="submit" class="al-btn al-primary">移动</button></div></form>`);
    $('#alMoveCancel').onclick = closeModal;
    $('#alMoveForm').onsubmit = async event => {
      event.preventDefault();
      const folderId = $('#alMoveFolder').value;
      if ((asset.folder_id || '') === folderId) { closeModal(); return toast('素材已在该文件夹'); }
      const button = $('button[type=submit]', event.target); button.disabled = true;
      try { await api(`/api/assets/${encodeURIComponent(asset.id)}`, patch({ folder_id: folderId })); closeModal(); await refresh(); toast('素材已移动'); }
      catch (error) { formError(error.message); button.disabled = false; }
    };
  }
  function renameAsset(asset) {
    if (!asset) return;
    modal(`<div class="al-modal-kicker">素材管理</div><h2>重命名素材</h2><form id="alRenameForm" class="al-form"><label>素材名称<input id="alRenameName" required maxlength="160" value="${esc(asset.name || '')}"></label><p id="alFormError" class="al-error" role="alert" hidden></p><div class="al-modal-actions"><span></span><button type="button" class="al-btn" id="alRenameCancel">取消</button><button type="submit" class="al-btn al-primary">保存</button></div></form>`);
    $('#alRenameCancel').onclick = closeModal;
    const input = $('#alRenameName'); input.focus(); input.select();
    $('#alRenameForm').onsubmit = async event => {
      event.preventDefault();
      const name = input.value.trim();
      if (!name) return formError('请输入素材名称');
      if (name === asset.name) { closeModal(); return; }
      const button = $('button[type=submit]', event.target); button.disabled = true;
      try { await api(`/api/assets/${encodeURIComponent(asset.id)}`, patch({ name })); closeModal(); await refresh(); toast('素材已重命名'); }
      catch (error) { formError(error.message); button.disabled = false; }
    };
  }
  function editAsset(asset) {
    if (!asset) return;
    const uses = asset.usage_projects || [];
    modal(`<div class="al-modal-kicker">素材管理</div><h2>${esc(asset.name)}</h2><form id="alAssetForm" class="al-form">${metadataFields(asset)}<div class="al-usage">${Number(asset.usage_count || 0) ? `已被 ${Number(asset.usage_count)} 处引用${uses.length ? ` · ${uses.map(project => esc(project.name)).join('、')}` : ''}。已引用素材受删除保护。` : '调整名称、标签和文件夹，不会改变画布中的素材内容。'}</div><p id="alFormError" class="al-error" role="alert" hidden></p><div class="al-modal-actions"><button type="button" class="al-btn al-danger" id="alDeleteOne">删除素材</button><span></span><button type="button" class="al-btn" id="alAssetCancel">取消</button><button type="submit" class="al-btn al-primary">保存更改</button></div></form>`);
    $('#alAssetCancel').onclick = closeModal;
    $('#alDeleteOne').onclick = () => deleteAssets([asset.id]);
    $('#alAssetForm').onsubmit = async event => { event.preventDefault(); const values = metadataValues(); if (!values.name) return formError('请输入素材名称'); const button = $('button[type=submit]', event.target); button.disabled = true; try { await api(`/api/assets/${encodeURIComponent(asset.id)}`, patch(values)); closeModal(); await refresh(); toast('素材信息已保存'); } catch (error) { formError(error.message); button.disabled = false; } };
  }
  function preview(asset, onPick) {
    if (!asset) return;
    stopPreviews();
    const kind = kindOf(asset), url = safeUrl(asset.url), label = TYPES[kind]?.[0] || '素材';
    const media = kind === 'image' ? `<img class="al-preview-backdrop" src="${url}" alt="" aria-hidden="true"><img class="al-preview-original" src="${url}" alt="${esc(asset.name)}">`
      : kind === 'video' ? `<video class="al-preview-backdrop" src="${url}" muted preload="auto" playsinline aria-hidden="true" tabindex="-1"></video><video class="al-preview-original" src="${url}" controls preload="auto" playsinline></video>`
      : `${thumbnail(asset,true)}`;
    showModal(`<div class="al-preview-layout" id="alPreviewLayout"><div class="al-preview-stage al-preview-stage-${kind}">${media}<div class="al-preview-wait">${loadingMarkup(`正在加载${label}…`)}</div></div><aside class="al-preview-sidebar"><h2>${label}</h2><div class="al-preview-info">${loadingMarkup('正在读取素材信息…')}</div><footer class="al-preview-actions"><button type="button" id="alPreviewSave">${icon(asset.in_library?'libraryRemove':'library')}${asset.in_library?'移出资产库':'保存至资产库'}</button><button type="button" id="alPreviewAdd">${icon('plus')}${onPick?'选择素材':'添加到画布'}</button><button type="button" class="al-preview-delete" id="alPreviewDelete">${icon('trash')}删除</button><button type="button" id="alPreviewDownload" ${url?'':'disabled'}>${icon('download')}下载</button></footer></aside></div>`, 'asset-modal asset-preview-modal');
    const root = $('#alPreviewLayout'), original = $('.al-preview-original', root), background = $('.al-preview-backdrop', root), wait = $('.al-preview-wait', root);
    const updateSaveButton = value => {
      const button=$('#alPreviewSave',root), current=byId(asset.id)||value;
      button.disabled=saving.has(asset.id);
      button.innerHTML=icon(current.in_library?'libraryRemove':'library')+(current.in_library?'移出资产库':'保存至资产库');
    };
    $('#alPreviewSave',root).onclick=async event=>{const button=event.currentTarget;button.disabled=true;button.textContent=(byId(asset.id)||asset).in_library?'正在移出…':'正在保存…';await setLibraryMembership(asset);if(root.isConnected)updateSaveButton(asset);};
    $('#alPreviewAdd',root).onclick=()=>{if(!onPick&&!state.project)return toast('请先创建或打开一个项目');closeModal();if(onPick)onPick(byId(asset.id)||asset);else ctx.onAddAsset(byId(asset.id)||asset);};
    $('#alPreviewDelete').onclick = () => deleteAssets([asset.id]);
    $('#alPreviewDownload').onclick = event => downloadAsset(asset, event.currentTarget);
    let dimensions = '', duration = Number(asset.duration) || 0;
    const updateMedia = () => {
      wait.hidden = true;
      const width = original?.naturalWidth || original?.videoWidth, height = original?.naturalHeight || original?.videoHeight;
      if (width && height) dimensions = `${width} × ${height}`;
      if (Number.isFinite(original?.duration)) duration = original.duration;
      const field = $('#alPreviewDimensions', root); if (field) field.textContent = dimensions || '—';
      const length = $('#alPreviewDuration', root); if (length) length.textContent = duration ? `${duration.toFixed(1)} 秒` : '—';
    };
    if (original) {
      original.addEventListener(kind === 'image' ? 'load' : 'loadeddata', updateMedia);
      if (original.complete && original.naturalWidth || original.readyState >= 2) updateMedia();
      original.addEventListener('error', () => {
        wait.hidden = false;wait.innerHTML = '<span>媒体加载失败</span><button type="button">重新加载</button>';
        wait.querySelector('button').onclick = () => { wait.innerHTML = loadingMarkup(`正在加载${label}…`); if(kind==='video')original.load();else original.src=asset.url; };
      });
      if (kind === 'video') {
        original.addEventListener('play', () => { background.currentTime=original.currentTime; background.play().catch(()=>{}); });
        original.addEventListener('pause', () => background.pause());
        original.addEventListener('seeked', () => { background.currentTime=original.currentTime; });
      }
    } else wait.hidden = true;
    const drawInfo = details => {
      if (!root.isConnected) return;
      if(!saving.has(asset.id))updateSaveButton(details);
      const generation = details.generation, prompt = generation?.prompt || '', refs = generation?.references || [];
      const created = details.created_at ? new Date(Number(details.created_at)*1000).toLocaleString('zh-CN',{hour12:false}) : '未记录';
      const badges = [generation?.model_name || generation?.model_id, generation?.aspect_ratio, generation?.size || generation?.resolution, generation?.duration ? `${generation.duration} 秒` : ''].filter(Boolean);
      $('.al-preview-info',root).innerHTML = `<dl class="al-preview-properties"><div><dt>名称</dt><dd>${esc(details.name)}</dd></div><div><dt>创作者</dt><dd>${esc(details.creator_name||'未记录')}</dd></div><div><dt>创建于</dt><dd>${esc(created)}</dd></div><div><dt>来源</dt><dd>${sourceOf(details)==='generated'?'AI 生成':'上传素材'}</dd></div><div><dt>尺寸</dt><dd id="alPreviewDimensions">${esc(dimensions||'—')}</dd></div>${kind==='video'||kind==='audio'?`<div><dt>时长</dt><dd id="alPreviewDuration">${duration?`${duration.toFixed(1)} 秒`:'—'}</dd></div>`:''}<div><dt>文件大小</dt><dd>${esc(bytes(details.size_bytes)||'—')}</dd></div></dl><div class="al-preview-section-heading"><span>生成信息</span>${prompt?`<button type="button" id="alPreviewCopy" aria-label="复制生成提示词" title="复制提示词">${icon('copy')}</button>`:''}</div><p class="al-preview-prompt ${prompt?'':'is-empty'}">${esc(prompt||'暂无生成信息')}</p>${badges.length?`<div class="al-preview-badges">${badges.map(text=>`<span title="${esc(text)}">${esc(text)}</span>`).join('')}</div>`:''}${refs.length?`<div class="al-preview-section-heading">参考素材</div><div class="al-preview-references">${refs.map(ref=>`<button type="button" data-preview-reference="${esc(ref.id)}" title="${esc(ref.name)}">${['image','prop'].includes(ref.kind)?`<img src="${safeUrl(ref.url)}" alt="${esc(ref.name)}">`:icon(ref.kind==='video'?'video':'audio')}</button>`).join('')}</div>`:''}${details.description?`<div class="al-preview-section-heading">素材说明</div><p class="al-preview-prompt">${esc(details.description)}</p>`:''}`;
      $('#alPreviewCopy',root)?.addEventListener('click',async()=>{try{await navigator.clipboard.writeText(prompt);toast('提示词已复制');}catch{toast('复制失败，请选择提示词手动复制');}});
      if(details.file_status==='failed'){
        $('.al-preview-info',root).insertAdjacentHTML('afterbegin',`<div class="al-error" role="alert">视频已生成，服务器保存文件失败。${details.file_error?`<p>${esc(details.file_error)}</p>`:''}<button type="button" id="alRetryFile">重试下载</button></div>`);
        $('#alRetryFile',root).onclick=async event=>{const button=event.currentTarget;button.disabled=true;const result=await retryFile(details);if(result&&root.isConnected)preview(result,onPick);else button.disabled=false;};
      }
      $$('[data-preview-reference]',root).forEach(button=>button.onclick=()=>preview(byId(button.dataset.previewReference)||refs.find(ref=>ref.id===button.dataset.previewReference)));
    };
    const loadInfo = async () => {
      try { const details=await api(`/api/assets/${encodeURIComponent(asset.id)}`);drawInfo(details); }
      catch(error) { if(root.isConnected){drawInfo(asset);$('.al-preview-info',root).insertAdjacentHTML('beforeend',`<div class="al-preview-detail-error" role="alert">${esc(error.message)}<button type="button" id="alPreviewRetry">重新读取</button></div>`);$('#alPreviewRetry',root).onclick=loadInfo;} }
    };
    loadInfo();
  }
  function rememberAsset(asset) {
    const index=state.assets.findIndex(item=>item.id===asset.id);
    if(index<0)state.assets.unshift(asset);else Object.assign(state.assets[index],asset);
    for(const task of Object.values(state.nodeTasks||{})){
      if(task.asset_id===asset.id||asset.kind==='video'&&task.id===asset.task_id){
        task.asset_id=asset.id;task.asset_save_status=asset.in_library?'saved':'history';
      }
    }
    if(!asset.in_library&&ui.collection==='library')ui.selected.delete(asset.id);
    document.dispatchEvent(new CustomEvent('asset-library-change',{detail:asset}));
    changed();return asset;
  }
  const saving = new Map();
  async function setLibraryMembership(value, inLibrary) {
    const id=typeof value==='string'?value:value?.id;
    if(!id){toast('请先生成或导入内容');return null;}
    if(saving.has(id))return saving.get(id);
    inLibrary??=!(byId(id)||value)?.in_library;
    const promise=(async()=>{
      try {const asset=rememberAsset(await api(`/api/assets/${encodeURIComponent(id)}/library`,inLibrary?json({}):{method:'DELETE'}));toast(inLibrary?'已保存至资产库':'已移出资产库，仍保留在生成历史');return asset;}
      catch(error){toast(error.message);return null;}
    })();
    saving.set(id,promise);try{return await promise;}finally{saving.delete(id);redraw();}
  }
  async function retryFile(asset) {
    try {const result=rememberAsset(await api(`/api/assets/${encodeURIComponent(asset.id)}/retry-file`,json({})));toast(result.file_status==='cached'?'视频文件已下载，已保留在生成历史':result.file_error||'文件下载失败，请稍后重试');return result;}
    catch(error){toast(error.message);return null;}
  }
  function saveNode(node) { return setLibraryMembership(node?.assetId || node?.asset_id); }
  async function saveTask(task, inLibrary) {
    if (!task?.id || task.status !== 'done') {toast('视频生成完成后可以保存至资产库');return null;}
    try {const history=await api(`/api/tasks/${encodeURIComponent(task.id)}/history-asset`,json({}));const saved=await setLibraryMembership(history,inLibrary??!history.in_library);if(saved){task.asset_id=saved.id;task.asset_save_status=saved.in_library?'saved':'history';task.status_message='生成完成';task.video_cache_status=saved.file_status;task.video_url=saved.url;changed();}return saved;}
    catch(error){toast(error.message);return null;}
  }

  function editFolder(folder) {
    modal(`<div class="al-modal-kicker">素材分类</div><h2>${folder ? '重命名文件夹' : '新建文件夹'}</h2><form id="alFolderForm" class="al-form"><label>文件夹名称<input id="alFolderName" maxlength="80" required value="${esc(folder?.name || '')}" placeholder="例如：企业宣传、桥梁工程、人物参考"></label><p id="alFormError" class="al-error" role="alert" hidden></p><div class="al-modal-actions"><span></span><button type="button" class="al-btn" id="alFolderCancel">取消</button><button type="submit" class="al-btn al-primary">保存</button></div></form>`);
    $('#alFolderCancel').onclick = closeModal;
    $('#alFolderForm').onsubmit = async event => { event.preventDefault(); const name = $('#alFolderName').value.trim(); if (!name) return formError('请输入文件夹名称'); const button = $('button[type=submit]', event.target); button.disabled = true; try { const result = await api(folder ? `/api/asset-folders/${encodeURIComponent(folder.id)}` : '/api/asset-folders', folder ? patch({ name }) : json({ name })); if (!folder) ui.folder = result.id; closeModal(); await refresh(); toast('文件夹已保存'); } catch (error) { formError(error.message); button.disabled = false; } };
  }
  function deleteFolder() {
    const folder = ui.folders.find(item => item.id === ui.folder); if (!folder) return;
    modal(`<h2>删除文件夹「${esc(folder.name)}」</h2><p class="al-section-note">文件夹中的素材将移回「未分组」，素材文件与画布引用会保留。</p><p id="alFormError" class="al-error" role="alert" hidden></p><div class="al-modal-actions"><span></span><button class="al-btn" id="alDeleteCancel">取消</button><button class="al-btn al-danger" id="alDeleteConfirm">删除文件夹</button></div>`);
    $('#alDeleteCancel').onclick = closeModal;
    $('#alDeleteConfirm').onclick = async event => { event.target.disabled = true; try { await api(`/api/asset-folders/${encodeURIComponent(folder.id)}`, { method: 'DELETE' }); ui.folder = '*'; closeModal(); await refresh(); toast('文件夹已删除，素材已移回未分组'); } catch (error) { formError(error.message); event.target.disabled = false; } };
  }
  async function moveSelected() {
    const ids = [...ui.selected], folderId = $('#alBulkFolder')?.value || ''; if (!ids.length) return;
    const result = await Promise.allSettled(ids.map(id => api(`/api/assets/${encodeURIComponent(id)}`, patch({ folder_id: folderId }))));
    const failures = result.flatMap((item, index) => item.status === 'rejected' ? [{ id: ids[index], message: item.reason.message }] : []);
    ui.selected = new Set(failures.map(item => item.id));
    await refresh();
    toast(failures.length ? `已移动 ${ids.length - failures.length} 项，${failures.length} 项失败：${failures[0].message}` : `已移动 ${ids.length} 项素材`);
  }
  function deleteAssets(ids) {
    const assets = ids.map(byId).filter(Boolean); if (!assets.length) return;
    showModal(`<div class="delete-confirm"><div class="delete-confirm-heading"><span class="delete-confirm-icon">${icon('trash')}</span><div><h2 id="deleteConfirmTitle">删除 ${assets.length} 个素材？</h2><p>删除后无法恢复，请确认所选素材。</p></div></div><div class="delete-confirm-list">${assets.map(asset => `<div class="delete-confirm-item">${icon(TYPES[kindOf(asset)]?.[1] || 'image')}<span>${esc(asset.name)}</span></div>`).join('')}</div><p class="delete-confirm-note">被项目引用的素材会保留，并显示未删除原因。</p><p id="alFormError" class="al-error" role="alert" hidden></p><div class="modal-actions"><button type="button" class="secondary" id="alDeleteCancel">取消</button><button type="button" class="danger-fill" id="alDeleteConfirm">${icon('trash')}<span>确认删除</span></button></div></div>`, 'asset-modal delete-confirm-modal');
    $('#alDeleteCancel').onclick = closeModal;
    $('#alDeleteCancel').focus();
    $('#alDeleteConfirm').onclick = async event => {
      const button = event.currentTarget; button.disabled = true; button.textContent = '正在删除…';
      const results = await Promise.allSettled(assets.map(asset => api(`/api/assets/${encodeURIComponent(asset.id)}`, { method: 'DELETE' })));
      const failed = results.flatMap((result, index) => result.status === 'rejected' ? [{ asset: assets[index], message: result.reason.message }] : []);
      results.forEach((result,index)=>{if(result.status==='fulfilled')document.dispatchEvent(new CustomEvent('asset-deleted',{detail:assets[index]}));});
      ui.selected = new Set(failed.map(item => item.asset.id));
      await refresh();
      if (failed.length) { formError(`已删除 ${assets.length - failed.length} 项，以下 ${failed.length} 项未删除：\n${failed.map(item => `${item.asset.name}：${item.message}`).join('\n')}`); button.hidden = true; $('#alDeleteCancel').textContent = '关闭'; }
      else { closeModal(); toast(`已删除 ${assets.length} 项素材`); }
    };
  }

  function sendUpload(file, folder, onProgress, kind = '') {
    return new Promise((resolve, reject) => {
      const request = new XMLHttpRequest(), form = new FormData();
      form.append('file', file); if (state.project?.id) form.append('project_id', state.project.id); if (folder) form.append('folder_id', folder); if (kind) form.append('kind', kind);
      request.open('POST', '/api/assets');
      withMarketSession({}, '/api/assets').headers.forEach((value,key)=>request.setRequestHeader(key,value));
      request.upload.onprogress = event => { if (event.lengthComputable) onProgress(Math.min(99, Math.round(event.loaded / event.total * 100))); };
      request.onload = () => { let data; try { data = JSON.parse(request.responseText || '{}'); } catch { return reject(new Error('上传响应异常，请重试')); } if (request.status >= 200 && request.status < 300) resolve(marketResponseData(data)); else reject(new Error(typeof data.detail === 'string' ? data.detail : `上传失败（${request.status}）`)); };
      request.onerror = () => reject(new Error('网络中断，素材未上传完成')); request.onabort = () => reject(new Error('上传已取消'));
      request.send(form);
    });
  }
  async function uploadFiles(files, kind, onUploaded) {
    if (ui.uploading) return toast('请等待当前上传完成');
    const list = [...files]; if (!list.length) return;
    ui.uploading = true; ui.jobs = list.map(file => ({ name: file.name, progress: 0, status: 'pending' })); redraw();
    const completed = [], folder = ui.folder === '*' ? '' : ui.folder;
    for (let index = 0; index < list.length; index++) {
      const job = ui.jobs[index]; job.status = 'uploading'; redraw();
      try { const asset = await sendUpload(list[index], folder, progress => { job.progress = progress; const progressElement = $$('.al-upload-job progress')[index]; if (progressElement) progressElement.value = progress; const statusElement = $$('.al-upload-job small')[index]; if (statusElement) statusElement.textContent = `${progress}%`; }, kind === 'prop' ? 'prop' : ''); job.status = 'done'; job.progress = 100; completed.push(asset); }
      catch (error) { job.status = 'error'; job.error = error.message; }
      redraw();
    }
    ui.uploading = false; await refresh();
    if (onUploaded && completed.length) onUploaded(completed[0]);
    toast(`已上传 ${completed.length} 项${completed.length < list.length ? `，${list.length - completed.length} 项失败，请查看上传结果` : ''}`);
  }
  function upload(kind = ui.type, onUploaded) {
    if (kind === 'character') return editCharacter();
    const input = document.createElement('input'); input.type = 'file'; input.accept = Array.isArray(kind) ? kind.map(item=>ACCEPT[item]||'').join(',') : ACCEPT[kind] || ACCEPT.image; input.multiple = !onUploaded; input.hidden = true; document.body.append(input);
    input.onchange = () => { const files = [...input.files]; input.remove(); uploadFiles(files, kind, onUploaded); };
    input.oncancel = () => input.remove(); input.click();
  }
  async function generateImage(options = {}) {
    if (!ctx.onGenerateImage) return toast('图片生成功能暂不可用，请刷新后重试');
    const projectId = state.project?.id || '';
    const folderId = ui.folders.some(folder => folder.id === ui.folder) ? ui.folder : '';
    try {
      await ctx.onGenerateImage({
        project_id: projectId,
        folder_id: folderId,
        asset_kind: options.asset_kind || (ui.type === 'prop' ? 'prop' : 'image'),
        onUse: async asset => { await refresh(); await options.onUse?.(asset); },
      });
    } catch (error) { toast(error.message || '图片生成页面打开失败，请重试'); }
  }
  async function generateAudio(options = {}) {
    if (!ctx.onGenerateAudio) return toast('音频生成功能暂不可用，请刷新后重试');
    const projectId = state.project?.id || '';
    const folderId = ui.folders.some(folder => folder.id === ui.folder) ? ui.folder : '';
    try {
      await ctx.onGenerateAudio({ project_id: projectId, folder_id: folderId,
        ...(options.onUse ? { onUse: async asset => { await refresh(); await options.onUse(asset); } } : {}),
      });
    } catch (error) { toast(error.message || '音频生成页面打开失败，请重试'); }
  }
  async function openPicker(options = {}) {
    pickerOptions = { kind: 'image', ...options }; ui.pickerCollection='history'; panelQuery = ''; panelMode = 'picker'; renderPicker();
    if (!ui.loaded) await refresh();
  }
  function renderPicker() {
    if (!pickerOptions) return;
    const kind = pickerOptions.kind, query = panelQuery.trim().toLowerCase();
    const assets = state.assets.filter(asset => (ui.pickerCollection==='history'||asset.in_library) && (Array.isArray(kind) ? kind.includes(kindOf(asset)) : kindOf(asset) === kind) && (!query || [asset.name, ...tagsOf(asset)].join(' ').toLowerCase().includes(query)));
    const label = Array.isArray(kind) ? '参考' : TYPES[kind]?.[0] || (kind === 'script' ? '脚本' : '文本'), focused = document.activeElement?.id === 'alPickerSearch', caret = document.activeElement?.selectionStart;
    ctx.openCanvasPanel(pickerOptions.title || `${label}资产库`, `选择${label}素材作为画布节点 · ${LIMITS[kind] || ''}`, `<div id="alPicker" class="al-picker"><div class="al-collection-tabs" role="tablist" aria-label="选择内容范围">${[["history","生成历史"],["library","资产库"]].map(([value,text])=>`<button type="button" role="tab" data-picker-collection="${value}" aria-selected="${ui.pickerCollection===value}">${text}</button>`).join("")}</div><div class="al-filter-row"><label class="al-search"><input id="alPickerSearch" value="${esc(panelQuery)}" placeholder="搜索${label}名称或标签"></label><button class="al-btn al-primary" id="alPickerUpload">${icon('upload','ui-icon-sm')}上传</button>${kind === 'image' || kind === 'prop' ? `<button type="button" class="al-btn al-ai-button" id="alPickerGenerate">${icon('wand','ui-icon-sm')}AI 生成图片</button>` : ''}</div>${kind === 'script' ? `<p class="al-section-note"><a href="${marketMediaUrl('/api/scripts/template')}" download>下载分镜 Excel 模板 ↗</a></p>` : ''}${uploadJobs()}<div class="al-picker-grid">${ui.loading||!ui.loaded&&!ui.error?loadingMarkup("正在读取素材…"):ui.error?`<div class="al-error" role="alert">${esc(ui.error)}<button type="button" id="alPickerRetry">重新加载</button></div>`:assets.length ? assets.map(asset => assetCard(asset, true)).join('') : `<div class="al-empty">${icon('search')}<h3>暂无匹配素材</h3><p>上传文件，或换一个关键词。</p></div>`}</div></div>`);
    if (kind === 'audio') {
      $('#alPicker .al-filter-row')?.insertAdjacentHTML('beforeend', `<button type="button" class="al-btn al-ai-button" id="alPickerGenerateAudio">${icon('sparkle','ui-icon-sm')}AI 生成音频</button>`);
    }
    const root = $('#alPicker');
    $$('[data-picker-collection]',root).forEach(button=>button.onclick=()=>{ui.pickerCollection=button.dataset.pickerCollection;renderPicker();});
    $('#alPickerRetry')?.addEventListener('click',()=>refresh());
    $('#alPickerSearch').oninput = event => { panelQuery = event.target.value; renderPicker(); };
    const options = pickerOptions, projectId = state.project?.id || '';
    if ($('#alPickerGenerate')) $('#alPickerGenerate').onclick = () => generateImage({ asset_kind: kind, onUse: asset => {
      if (options !== pickerOptions || panelMode !== 'picker' || !$('#alPicker') || projectId !== (state.project?.id || '')) return;
      if (!['image', 'prop'].includes(kindOf(asset))) return toast('生成结果不是图片，请重新选择素材');
      if (options.onPick) return options.onPick(asset);
      if (!state.project) return toast('图片已加入生成历史，请先打开项目再添加到画布');
      return ctx.onAddAsset(asset);
    } });
    if ($('#alPickerGenerateAudio')) $('#alPickerGenerateAudio').onclick = () => generateAudio({ onUse: asset => {
      if (options !== pickerOptions || panelMode !== 'picker' || !$('#alPicker') || projectId !== (state.project?.id || '')) return;
      if (kindOf(asset) !== 'audio') return toast('生成结果不是音频，请重新选择素材');
      if (options.onPick) return options.onPick(asset);
      if (!state.project) return toast('音频已加入生成历史，请先打开项目再添加到画布');
      return ctx.onAddAsset(asset);
    } });
    $('#alPickerUpload').onclick = () => upload(kind, asset => { if (options !== pickerOptions || panelMode !== 'picker') return; if (Array.isArray(kind) ? kind.includes(kindOf(asset)) : kindOf(asset) === kind) options.onPick ? options.onPick(asset) : ctx.onAddAsset(asset); else toast('文件已保存，请选择对应类型的素材'); });
    bindCards(root, asset => { if (pickerOptions.onPick) pickerOptions.onPick(asset); else ctx.onAddAsset(asset); });
    $$('[data-al-action="clear-jobs"]', root).forEach(button => button.onclick = () => { ui.jobs = []; renderPicker(); });
    if (focused) { const input = $('#alPickerSearch'); input.focus(); if (typeof caret === 'number') input.setSelectionRange(caret, caret); }
  }
  function openCharacters() { toast('请使用图片素材保存人物参考'); }
  const editCharacter = openCharacters;
  document.addEventListener('pointerdown', event => { if (event.target.closest('.al-card-menu, .al-card-more')) return; closeAssetMenus(); });
  document.addEventListener('keydown', event => { if (event.key === 'Escape') closeAssetMenus(); });
  return { render, openPicker, openCharacters, editCharacter, saveNode, saveTask, setLibraryMembership, retryFile, refresh, upload, stopPreviews, preview };
}

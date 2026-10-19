import { setSurfaceVisible, surfaceIsOpen } from './canvas-motion.js?v=liblib-detail-2';
import { createNodeContext } from './node-context.js?v=library-toggle-1';
import { createVisualStylePicker } from './visual-style-picker.js?v=4';
// Canvas chrome shares the existing editors and persistence with the application.
// Its screen-space overlays never participate in node/world coordinates.
export function createCanvasWorkspace(api) {
  const { state, icon, escapeHtml: esc } = api;
  const $ = selector => document.querySelector(selector);
  const canvas = $('#canvas');
  const inspector = $('#inspector');
  const button = (id, name, glyph, extra = '') => `<button id="${id}" type="button" title="${name}" aria-label="${name}" ${extra}>${icon(glyph)}</button>`;
  let frame = 0, dismissed = false, lastSelection, mode = 'workflow', boardKey = '', mapBounds, paletteAnchor;

  $('#projectBreadcrumb').insertAdjacentHTML('afterbegin', `<button id="workspaceMenu" type="button" title="项目与导航" aria-label="项目与导航" aria-expanded="false"><img class="workspace-menu-logo" src="/brand-logo-transparent.png?v=brand-2" alt="" aria-hidden="true">${icon('panel')}</button>`);
  $('header').insertAdjacentHTML('beforeend', `<div class="workspace-modes" role="tablist" aria-label="画布视图"><button role="tab" id="workflowMode" aria-selected="true">${icon('link')}工作流</button><button role="tab" id="storyboardMode" aria-selected="false">${icon('grid')}故事板</button></div>`);
  canvas.insertAdjacentHTML('beforeend', `
    <div class="workspace-chrome canvas-utilities" role="toolbar" aria-label="画布视图工具">
      <button id="workspaceAssets" class="utility-label" type="button">${icon('panel')}<span>资产管理</span></button><i></i>
      ${button('arrangeCanvas', '整理画布 · Alt+Shift+F', 'arrange')}
      ${button('toggleMinimap', '切换小地图', 'minimap', 'aria-pressed="false"')}
      ${button('toggleEdges', '隐藏节点连线', 'connections', 'aria-pressed="false"')}
      ${button('toggleSnap', '网格吸附', 'magnet', 'aria-pressed="false"')}
      <button id="workspaceZoom" aria-expanded="false" aria-controls="workspaceZoomMenu"><span id="workspaceZoomValue">100%</span>${icon('chevronDown')}</button>
    </div>
    <div id="workspaceZoomMenu" class="workspace-chrome workspace-popover zoom-popover" hidden>
      <label>缩放比例 <input id="workspaceZoomInput" type="number" min="5" max="800" step="5" aria-label="缩放比例"><span>%</span></label>
      <button data-zoom="in">放大 <kbd>Ctrl +</kbd></button><button data-zoom="out">缩小 <kbd>Ctrl −</kbd></button>
      <button data-zoom="fit">适合屏幕 <kbd>Ctrl 0</kbd></button><div class="workspace-menu-line"></div>
      <button data-zoom="50">缩放至 50%</button><button data-zoom="100">缩放至 100%</button><button data-zoom="800">缩放至 800%</button>
    </div>
    <div id="workspaceMinimap" class="workspace-chrome workspace-minimap" hidden><div><span>画布概览</span>${button('closeMinimap', '关闭小地图', 'close')}</div><svg viewBox="0 0 200 115" role="img" aria-label="画布节点小地图"></svg></div>
    <div id="workspaceEmpty" class="workspace-chrome workspace-empty" hidden><div class="empty-symbol">${icon('plus')}</div><h2>从一个灵感开始</h2><p>双击画布添加节点，将想法连接成作品</p><div><button data-empty-add>添加节点</button><button data-empty-upload>${icon('upload')}上传素材</button></div></div>
    <div class="workspace-chrome workspace-caption"><span id="workspaceNodeCount"></span><span>双击添加节点 · 空格拖动画布</span></div>
    <section id="workspaceBoard" class="workspace-chrome workspace-board" aria-label="故事板" hidden></section>
    <button id="workspaceAgent" class="workspace-chrome workspace-agent" title="Agent 创作" aria-label="Agent 创作"><span class="agent-particles" aria-hidden="true">${Array.from({length:10},(_,i)=>`<i style="--x:${[18,68,38,82,52,26,74,44,60,12][i]}%;--drift:${[5,-7,9,-4,6,-8,4,-6,8,3][i]}px;--duration:${2.1+i%4*.35}s;--delay:${-i*.39}s;--size:${i%3===0?3:2}px"></i>`).join('')}</span></button>
    <input id="workspaceUploadInput" type="file" accept="image/*,video/*,audio/*" multiple hidden>
    <div id="workspaceSearch" class="workspace-chrome workspace-search workspace-popover" hidden><label>${icon('search')}<input placeholder="搜索画布节点…" aria-label="搜索画布节点"></label><div id="workspaceSearchResults"></div></div>
  `);
  canvas.append(inspector);
  inspector.insertAdjacentHTML('afterbegin', `<div class="floating-editor-head"><div>${button('editorClose', '收起节点编辑', 'close')}</div></div>`);
  inspector.setAttribute('aria-label', '节点编辑面板');
  inspector.setAttribute('role', 'region');
  inspector.classList.add('floating-editor');
  // Keep reference actions beside the prompt; original inputs keep IDs and handlers.
  const fields = $('#nodeEditorFields');
  const referenceRow = document.createElement('div');
  referenceRow.className = 'composer-reference-row';
  referenceRow.append($('#chooseReferenceBtn'));
  fields.prepend(referenceRow);
  const stylePicker=createVisualStylePicker({api:api.request,escapeHtml:esc,icon,onError:api.toast});
  stylePicker.button(referenceRow,{getValue:()=>api.selectedNode()?.settings?.visual_style,onChange:value=>{
    const node=api.selectedNode();if(node?.type!=='video')return;
    node.settings={...node.settings,visual_style:value};api.scheduleSave();
  }});
  const parameters = document.createElement('div');
  parameters.className = 'composer-parameters';
  for (const id of ['modelSelect', 'ratioSelect', 'resolutionSelect', 'durationSelect']) parameters.append($('#' + id));
  $('#generateBtn').before(parameters);
  $('#generateBtn .generate-label').textContent = '生成视频';
  $('#generateBtn .generate-hint').textContent = '';
  const nodeContext = createNodeContext(api, canvas, inspector, refresh);
  for (const [id, glyph, label] of [['dockAddNode','plus','添加节点'],['dockMaterial','materials','资产库'],['dockHistory','clock','生成历史'],['dockShortcuts','keyboard','快捷键'],['dockHelp','help','教程']]) {
    $('#' + id).querySelector(':scope > svg').outerHTML = icon(glyph);
    $('#' + id).title = label;
    $('#' + id).setAttribute('aria-label', label);
    $('#' + id).querySelector('.dock-tooltip').textContent = label;
  }
  $('#canvasPanel').addEventListener('keydown', event => {
    if ($('#canvasPanel').dataset.kind !== 'palette') return;
    if (event.key === 'Escape') { event.preventDefault(); api.closeCanvasPanel(); $('#dockAddNode').focus(); }
    if (!['ArrowDown','ArrowUp','Home','End'].includes(event.key)) return;
    event.preventDefault();
    const items = [...$('#canvasPanel').querySelectorAll('button:not(#canvasPanelClose)')];
    let index = items.indexOf(document.activeElement);
    index = event.key === 'Home' ? 0 : event.key === 'End' ? items.length-1 : (index+(event.key === 'ArrowDown'?1:-1)+items.length)%items.length;
    items[index]?.focus();
  });
  const connect = $('#dockConnect');
  connect.classList.add('connection-tool');
  const help = $('#dockHelp');
  help.classList.add('tutorial-tool');

  function closeNavigation() {
    document.body.classList.remove('workspace-nav-open');
    $('#workspaceMenu').setAttribute('aria-expanded', 'false');
  }
  $('#workspaceMenu').onclick = () => {
    const open = document.body.classList.toggle('workspace-nav-open');
    $('#workspaceMenu').setAttribute('aria-expanded', String(open));
    if (open) api.setRailState('sidebar', false, false);
  };
  $('.sidebar').addEventListener('click', event => {
    if (event.target.closest('[data-view],#projectList button,#newProject')) closeNavigation();
  });
  let agentActivation = null;
  $('#workspaceAgent').onclick = () => {
    if (agentActivation !== null) return;
    const orb = $('#workspaceAgent');
    orb.classList.add('is-activating');orb.setAttribute('aria-busy','true');
    agentActivation = setTimeout(() => {
      agentActivation = null;orb.classList.remove('is-activating');orb.removeAttribute('aria-busy');
      if (state.view === 'canvas') api.toggleCanvasAgent();
    },matchMedia('(prefers-reduced-motion: reduce)').matches ? 0 : 560);
  };
  $('#workspaceAssets').onclick = () => api.showView('assets');
  $('#editorClose').onclick = () => {
    dismissed = true; setSurfaceVisible(inspector, false);
    document.querySelectorAll('#canvasStage .node.selected').forEach(e => e.focus({ preventScroll: true }));
  };
  $('#workspaceZoom').onclick = () => togglePopover('workspaceZoomMenu', 'workspaceZoom');
  function togglePopover(id, trigger) {
    const el = $('#' + id), open = !surfaceIsOpen(el); setSurfaceVisible(el, open);
    if (trigger) $('#' + trigger).setAttribute('aria-expanded', String(open));
    if (open && id === 'workspaceZoomMenu') $('#workspaceZoomInput').value = Math.round(state.zoom * 100);
  }
  function zoomTo(value) {
    const next = Math.max(.05, Math.min(8, value));
    const x = canvas.clientWidth / 2, y = canvas.clientHeight / 2;
    state.panX = x - (x - state.panX) / state.zoom * next;
    state.panY = y - (y - state.panY) / state.zoom * next;
    api.setZoom(next); api.scheduleSave();
  }
  $('#workspaceZoomMenu').onclick = event => {
    const action = event.target.closest('[data-zoom]')?.dataset.zoom;
    if (!action) return;
    if (action === 'fit') { api.fitCanvas(); api.scheduleSave(); }
    else zoomTo(action === 'in' ? state.zoom * 1.1 : action === 'out' ? state.zoom / 1.1 : Number(action) / 100);
    setSurfaceVisible($('#workspaceZoomMenu'), false); $('#workspaceZoom').setAttribute('aria-expanded', 'false');
  };
  $('#workspaceZoomInput').onchange = event => {
    if (Number.isFinite(event.target.valueAsNumber)) zoomTo(event.target.valueAsNumber / 100);
    event.target.value = Math.round(state.zoom * 100);
  };
  $('#toggleEdges').onclick = () => {
    const hidden = canvas.classList.toggle('edges-hidden');
    $('#toggleEdges').setAttribute('aria-pressed', String(hidden));
    $('#toggleEdges').title = hidden ? '显示节点连线' : '隐藏节点连线';
    $('#toggleEdges').setAttribute('aria-label', $('#toggleEdges').title);
  };
  $('#toggleSnap').onclick = () => {
    state.snapToGrid = !state.snapToGrid;
    $('#toggleSnap').setAttribute('aria-pressed', String(state.snapToGrid));
    api.toast(state.snapToGrid ? '已开启网格吸附，拖动节点时对齐网格' : '已关闭网格吸附');
  };
  $('#toggleMinimap').onclick = () => { setSurfaceVisible($('#workspaceMinimap'), !surfaceIsOpen($('#workspaceMinimap'))); refresh(); };
  $('#closeMinimap').onclick = () => { setSurfaceVisible($('#workspaceMinimap'), false); refresh(); };
  $('#arrangeCanvas').onclick = arrange;
  function arrange() {
    if (!state.nodes.length) return;
    if(api.arrangeGroupedCanvas?.())return;
    // Longest-path ranks in a DAG; cycles fall into one final column, without a loop.
    const indegree = new Map(state.nodes.map(n => [n.id, 0]));
    const ranks = new Map(state.nodes.map(n => [n.id, 0]));
    const outgoing = new Map(state.nodes.map(n => [n.id, []]));
    for (const e of state.edges) if (indegree.has(e.from) && indegree.has(e.to)) {
      indegree.set(e.to, indegree.get(e.to) + 1); outgoing.get(e.from).push(e.to);
    }
    const queue = state.nodes.filter(n => !indegree.get(n.id)).map(n => n.id), visited = new Set();
    for (let i = 0; i < queue.length; i++) {
      const id = queue[i]; visited.add(id);
      for (const to of outgoing.get(id)) {
        ranks.set(to, Math.max(ranks.get(to), ranks.get(id) + 1));
        indegree.set(to, indegree.get(to) - 1); if (!indegree.get(to)) queue.push(to);
      }
    }
    const cycleRank = Math.max(0, ...ranks.values()) + 1;
    for (const n of state.nodes) if (!visited.has(n.id)) ranks.set(n.id, cycleRank);
    const columns = new Map();
    for (const n of state.nodes) { const r = ranks.get(n.id); if (!columns.has(r)) columns.set(r, []); columns.get(r).push(n); }
    let x = 100;
    for (const [, nodes] of [...columns].sort(([a], [b]) => a - b)) {
      let y = 100;
      for (const n of nodes) { n.x = x; n.y = y; y += api.nodeFootprint(n).height + 100; }
      x += Math.max(...nodes.map(n => api.nodeFootprint(n).width)) + 180;
    }
    api.renderNodes(); api.fitCanvas(); api.scheduleSave(); api.toast('已按节点连接关系整理画布');
  }
  function minimap() {
    const host = $('#workspaceMinimap');
    $('#toggleMinimap').setAttribute('aria-pressed', String(surfaceIsOpen(host)));
    if (host.hidden) return;
    const nodes = state.nodes;
    const left = Math.min(0, ...nodes.map(n => n.x)) - 50;
    const top = Math.min(0, ...nodes.map(n => n.y)) - 50;
    const right = Math.max(600, ...nodes.map(n => n.x + api.nodeFootprint(n).width)) + 50;
    const bottom = Math.max(400, ...nodes.map(n => n.y + api.nodeFootprint(n).height)) + 50;
    const scale = Math.min(200 / (right - left), 115 / (bottom - top));
    mapBounds = { left, top, scale };
    const rect = (x, y, w, h, cls) => `<rect class="${cls}" x="${(x-left)*scale}" y="${(y-top)*scale}" width="${w*scale}" height="${h*scale}" rx="1"/>`;
    host.querySelector('svg').innerHTML = nodes.map(n => { const size = api.nodeFootprint(n); return rect(n.x,n.y,size.width,size.height,n.id === state.selectedNodeId ? 'map-node selected' : 'map-node'); }).join('') + rect(-state.panX/state.zoom,-state.panY/state.zoom,canvas.clientWidth/state.zoom,canvas.clientHeight/state.zoom,'map-viewport');
  }
  $('#workspaceMinimap svg').onclick = event => {
    if (!mapBounds) return;
    const r = event.currentTarget.getBoundingClientRect();
    const x = (event.clientX-r.left)/r.width*200/mapBounds.scale+mapBounds.left;
    const y = (event.clientY-r.top)/r.height*115/mapBounds.scale+mapBounds.top;
    state.panX = canvas.clientWidth/2-x*state.zoom; state.panY = canvas.clientHeight/2-y*state.zoom;
    api.setZoom(state.zoom); api.scheduleSave();
  };
  function setMode(next) {
    mode = next; setSurfaceVisible($('#workspaceBoard'), next === 'storyboard');
    canvas.classList.toggle('storyboard-active', next === 'storyboard');
    $('#workflowMode').setAttribute('aria-selected', String(next === 'workflow'));
    $('#storyboardMode').setAttribute('aria-selected', String(next === 'storyboard'));
    refresh();
  }
  $('#workflowMode').onclick = () => setMode('workflow');
  $('#storyboardMode').onclick = () => setMode('storyboard');
  function renderBoard() {
    if (mode !== 'storyboard') return;
    const key = JSON.stringify([state.project?.id, state.nodes, state.nodeTasks]);
    if (key === boardKey) return; boardKey = key;
    const shots = state.nodes.filter(n => ['video','video-asset','asset','script'].includes(n.type)).sort((a,b)=>a.x-b.x||a.y-b.y);
    $('#workspaceBoard').innerHTML = `<div class="board-heading"><div><small>STORYBOARD</small><h1>${esc(state.project?.name || '故事板')}</h1><p>${shots.length} 个画面 · 按画布顺序浏览，点击返回节点编辑</p></div><button id="boardAdd">${icon('plus')}添加节点</button></div><div class="board-grid">${shots.length ? shots.map((n,i) => {
      const asset = state.assets.find(a => a.id === n.assetId), task = state.nodeTasks[n.id];
      const image = asset?.kind === 'image' ? asset.url : '';
      const video = task?.video_url || (asset?.kind === 'video' ? asset.url : '');
      const description = n.type === 'video' ? api.videoInput(n).prompt : n.type === 'script' ? n.script_data?.title || '点击编写分镜脚本' : asset?.name || n.title || '点击画面，继续你的创作';
      return `<article class="board-card"><div class="board-media">${image ? `<img src="${esc(image)}" alt="${esc(n.title)}">` : video ? `<video src="${esc(video)}" controls preload="metadata" playsinline></video>` : icon(n.type === 'script' ? 'script' : 'video')}</div><button data-board-node="${esc(n.id)}"><span class="board-number">${String(i+1).padStart(2,'0')}</span><span><b>${esc(n.title || '未命名画面')}</b><small>${esc(n.settings?.aspect_ratio || '16:9')}${['video','video-asset'].includes(n.type) ? ` · ${n.settings?.duration || 5}s` : ''}</small></span>${icon('arrowUpRight')}</button><p>${esc(description.slice(0,120))}</p></article>`;
    }).join('') : '<div class="board-empty">还没有画面，添加图片、视频或脚本节点开始创作。</div>'}</div>`;
    $('#boardAdd').onclick = () => { setMode('workflow'); api.showNodePalette(); };
    document.querySelectorAll('[data-board-node]').forEach(b => b.onclick = () => {
      const node = state.nodes.find(n=>n.id===b.dataset.boardNode); setMode('workflow'); api.updateSelection(node.id); api.fitCanvas([node]); api.scheduleSave();
    });
  }
  const upload = () => $('#workspaceUploadInput').click();
  $('#workspaceUploadInput').onchange = async event => {
    const files = [...event.target.files]; if (!files.length) return;
    if (!state.project) { api.toast('请先创建画布'); return; }
    const projectId = state.project.id;
    try {
      for (const file of files) {
        const form = new FormData(); form.append('file', file); form.append('project_id', projectId);
        const data = await api.request('/api/assets', { method: 'POST', body: form });
        if (state.project?.id === projectId) api.addLibraryAsset(data);
      }
      api.toast('素材已上传并添加到画布');
    } catch (error) { api.toast(error.message); } finally { event.target.value = ''; }
  };
  $('[data-empty-add]').onclick = () => api.showNodePalette();
  $('[data-empty-upload]').onclick = upload;
  function searchNodes() {
    const query = $('#workspaceSearch input').value.trim().toLowerCase();
    const matches = state.nodes.filter(n => `${n.title||''} ${n.settings?.prompt||''}`.toLowerCase().includes(query));
    $('#workspaceSearchResults').innerHTML = matches.length ? matches.map(n=>`<button data-search-node="${esc(n.id)}">${icon(({prompt:'text',asset:'image',video:'video',script:'script',audio:'audio'})[n.type]||'diamond')}<span>${esc(n.title||'未命名节点')}</span>${icon('arrowUpRight')}</button>`).join('') : '<p>没有找到节点</p>';
    document.querySelectorAll('[data-search-node]').forEach(b=>b.onclick=()=>{
      const node=state.nodes.find(n=>n.id===b.dataset.searchNode); setSurfaceVisible($('#workspaceSearch'), false);
      api.updateSelection(node.id); api.fitCanvas([node]); api.scheduleSave();
    });
  }
  $('#workspaceSearch input').oninput = searchNodes;
  function enhancePalette(position = null) {
    $('#canvasPanel').dataset.kind = 'palette';
    $('#canvasPanel').setAttribute('role', 'menu');
    $('#canvasPanel').setAttribute('aria-label', '添加节点');
    $('#canvasPanelSub').textContent = '选择节点，连接你的创作';
    $('#canvasPanel .canvas-panel-head').insertAdjacentHTML('beforeend', `<button id="paletteSearch" class="palette-search" aria-label="搜索画布节点" title="搜索画布节点 · Ctrl K">${icon('search')}</button>`);
    $('#canvasPanelBody').insertAdjacentHTML('beforeend', `<div class="palette-divider">添加资源</div><button id="paletteUpload" class="palette-resource">${icon('upload')}上传</button><button id="paletteHistory" class="palette-resource">${icon('clock')}从生成历史选择</button>`);
    $('#canvasPanelBody').querySelectorAll('button').forEach(b => b.setAttribute('role', 'menuitem'));
    $('#paletteUpload').onclick = upload;
    $('#paletteHistory').onclick = api.showCanvasHistoryPanel;
    $('#paletteSearch').onclick = () => { api.closeCanvasPanel(); setSurfaceVisible($('#workspaceSearch'), true); searchNodes(); $('#workspaceSearch input').focus(); };
    paletteAnchor = position ? { ...position } : null;
    $('#dockAddNode').setAttribute('aria-expanded', String(!paletteAnchor));
    positionPalette();
    setSurfaceVisible($('#canvasPanel'), true, { key: position ? `palette:${position.x}:${position.y}` : 'palette:dock' });
  }
  function positionPalette() {
    const panel = $('#canvasPanel');
    if (panel.hidden || panel.dataset.kind !== 'palette') return;
    const bounds = canvas.getBoundingClientRect(), add = $('#dockAddNode').getBoundingClientRect();
    const width = panel.offsetWidth, height = panel.offsetHeight;
    let left = paletteAnchor ? state.panX + paletteAnchor.x * state.zoom : add.left - bounds.left + add.width/2 - width/2;
    let top = paletteAnchor ? state.panY + paletteAnchor.y * state.zoom : $('.canvas-dock').getBoundingClientRect().top - bounds.top - height - 8;
    left = Math.max(8, Math.min(canvas.clientWidth-width-8, left));
    top = Math.max(8, Math.min(canvas.clientHeight-height-8, top));
    panel.style.left = left+'px'; panel.style.top = top+'px'; panel.style.bottom = 'auto'; panel.style.transform = 'none';
    panel.dataset.anchor = paletteAnchor ? 'pointer' : 'dock';
    panel.style.transformOrigin = paletteAnchor ? 'top left' : 'bottom center';
  }
  canvas.addEventListener('contextmenu', event => {
    if (event.target.closest('.node,.canvas-group,.workspace-chrome,.inspector,.canvas-panel,.canvas-dock')) return;
    event.preventDefault(); const r=canvas.getBoundingClientRect();
    api.showNodePalette({x:(event.clientX-r.left-state.panX)/state.zoom,y:(event.clientY-r.top-state.panY)/state.zoom});
  });
  document.addEventListener('pointerdown', event => {
    if (!event.target.closest('.sidebar,#workspaceMenu')) closeNavigation();
    if (!event.target.closest('#workspaceZoom,#workspaceZoomMenu')) { setSurfaceVisible($('#workspaceZoomMenu'), false); $('#workspaceZoom').setAttribute('aria-expanded','false'); }
    if (!event.target.closest('#workspaceSearch,#paletteSearch')) setSurfaceVisible($('#workspaceSearch'), false);
    if (surfaceIsOpen($('#canvasPanel')) && !event.target.closest('#canvasPanel,#dockAddNode')) api.closeCanvasPanel();
  });
  window.addEventListener('keydown', event => {
    if (state.view !== 'canvas' || event.isComposing || !$('#modalBackdrop').hidden || event.target.closest('input,textarea,select,[contenteditable="true"]') || document.querySelector('#imageGenerationOverlay,#audioGenerationOverlay')) return;
    if (event.key === 'Escape') { dismissed=true; setSurfaceVisible(inspector, false); closeNavigation(); setSurfaceVisible($('#workspaceSearch'), false); }
    if ((event.ctrlKey||event.metaKey) && event.code === 'KeyK') { event.preventDefault(); setSurfaceVisible($('#workspaceSearch'), true); searchNodes(); $('#workspaceSearch input').focus(); }
    if (event.altKey && event.shiftKey && event.code === 'KeyF') { event.preventDefault(); arrange(); }
    if ((event.ctrlKey||event.metaKey) && ['Equal','Minus','Digit0'].includes(event.code)) {
      event.preventDefault(); if(event.code==='Digit0') { api.fitCanvas(); api.scheduleSave(); } else zoomTo(state.zoom*(event.code==='Equal'?1.1:1/1.1));
    }
  });
  function refresh() { if (!frame) frame = requestAnimationFrame(paint); }
  function paint() {
    frame=0;api.refreshGroups?.();
    if (state.view !== 'canvas') {stylePicker.close();return;}
    const node = api.selectedNode();
    if (lastSelection !== node?.id) { stylePicker.close();dismissed=false; lastSelection=node?.id; }
    stylePicker.refresh();
    $('#workspaceZoomValue').textContent=`${Math.round(state.zoom*100)}%`;
    $('#workspaceNodeCount').textContent=`${state.nodes.length} 个节点`;
    $('#workspaceEmpty').hidden=!!state.nodes.length||mode!=='workflow';
    const dragging = !!state.drag?.moved;
    const editorVisible = !(dragging || dismissed || !node || mode!=='workflow' || state.selectedNodeIds?.size>1);
    if(!editorVisible||node.type!=='video')stylePicker.close();
    inspector.classList.toggle('gesture-hidden', dragging);
    setSurfaceVisible(inspector, editorVisible, { key: node?.id });
    nodeContext.paint(node, !dragging && !!node && mode==='workflow' && state.selectedNodeIds?.size<=1);
    if (editorVisible) {
      inspector.dataset.nodeType=node?.type||'';
      // A collapsed rail must not leave the re-used editor inert.
      api.setRailState('inspector', false, false);
      const card=node&&$(`.node[data-node="${CSS.escape(node.id)}"]`);
      const bounds=canvas.getBoundingClientRect(), r=card?.getBoundingClientRect();
      const width=node?.type==='script'?920:node?.type==='video'?640:520;
      const scale=state.zoom;
      const visibleWidth=Math.min(width,(canvas.clientWidth-24)/scale);
      inspector.style.width=visibleWidth+'px';
      inspector.style.maxWidth='none';
      inspector.style.maxHeight=Math.max(120,(canvas.clientHeight-100)/scale)+'px';
      inspector.style.transformOrigin='top left';
      inspector.style.transform=`scale(${scale})`;
      const desiredLeft=r?r.left-bounds.left+r.width/2-visibleWidth*scale/2:16;
      const desiredTop=r?r.bottom-bounds.top+12*scale:80;
      const left=Math.max(12,Math.min(canvas.clientWidth-visibleWidth*scale-12,desiredLeft));
      const top=Math.max(60,Math.min(canvas.clientHeight-inspector.offsetHeight*scale-40,desiredTop));
      inspector.style.left=left+'px'; inspector.style.top=top+'px';
    }
    positionPalette(); minimap(); renderBoard();
  }
  new ResizeObserver(refresh).observe(canvas);
  new ResizeObserver(refresh).observe(inspector);
  function onView(view) { closeNavigation(); if(view!=='canvas') inspector.hidden=true; else { dismissed=false; refresh(); } }
  function onSelect() { dismissed=false; refresh(); }
  refresh();
  return { refresh, onView, onSelect, enhancePalette };
}

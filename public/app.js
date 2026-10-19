import { createProjectLibrary } from './project-library.js?v=viewport-info-4';
import { createCanvasGroups, normalizeGroups, groupBounds } from './canvas-groups.js?v=unified-history-2';
import { createCanvasHistory } from './canvas-history.js?v=1';
import { beginRequest, withLoading } from './loading-state.js?v=1';
import { installNodePlayers } from './node-player.js?v=fullscreen-double-click-1';
import { describeTaskError, ARK_MODEL_MANAGEMENT_URL } from './task-errors.js?v=content-rejection-3';
import { resolveVideoInput, resolveVideoAssets, deriveVideoMode, materializeVideoAssets, referenceAssetId, scriptScenePrompt } from './node-inputs.js?v=node-frame-mode-2';
import { attachAssetMentions } from './asset-mentions.js?v=reference-sync-2';
import { createAssetLibrary } from './asset-library.js?v=asset-tab-motion-1';
import { mediaNodeBody, mediaNodeDetail, bindMediaNodeDetail } from './media-nodes.js?v=library-toggle-1';
import { RESOURCE_TYPES, PREPARATION_TYPES, preparationResources, resourceKind, acceptsResourceAsset } from './script-resources.js?v=icons-font-2';
import { createModelManager } from './model-manager.js?v=user-isolation-1';
import { createImageGenerator } from './image-generation.js?v=styles-2.3.0';
import { createAudioGenerator } from './audio-generation.js?v=library-toggle-1';
import { syncScriptResourceGraph, detachScriptResourceEdges } from './script-resource-graph.js?v=agent-canvas-clean-3';
import { icon } from './iconography.js?v=regenerate-1';
import { checkSessionResponse, createSessionControls } from './session.js?v=market-sdk-3';
import { initializeMarket, isMarketEntry, isMarketSessionRequired, navigateToMarketEntry, withMarketSession, marketResponseData, marketMediaUrl } from './market-session.js?v=market-sdk-3';
import { createAgentChat } from './agent-chat.js?v=styles-2.3.0';
import { createCanvasAgent } from './canvas-agent.js?v=canvas-agent-float-2';
import { createHomeComposer } from './home-composer.js?v=home-default-image-1';
import { createConversationResume } from './conversation-resume.js?v=1';
import { createHomeHub } from './home-hub.js?v=viewport-7';
import { createCanvasWorkspace } from './canvas-workspace.js?v=styles-2.3.0';
import { setSurfaceVisible, surfaceIsOpen } from './canvas-motion.js?v=liblib-detail-2';
import { createInlineNodeMedia } from './inline-node-media.js?v=styles-2.3.0';
import { installCanvasConnections } from './canvas-connections.js?v=1';

// Cancel browser image/link/text drag previews; canvas movement uses pointer events.
document.addEventListener('dragstart', event => event.preventDefault(), true);

// Use one durable pathname so normal in-app history changes retain market mode.
if (isMarketEntry() && location.pathname !== '/market' && new URLSearchParams(location.search).get('embedded') !== '1') {
  const entry = new URL(location.href);
  entry.pathname = '/market';
  entry.searchParams.delete('market');
  history.replaceState(null, '', entry.pathname + entry.search + entry.hash);
}

installNodePlayers();

let canvasWorkspace, canvasGroups, canvasAgent, homeComposer, homeHub, canvasConnections;
const conversationResume = createConversationResume();

const $ = (selector) => document.querySelector(selector);
const state = { project: null, projects: [], options: null, models: [], llm: null, selectedModelId: 'default-seedance-2.0-mini', assets: [], selectedAssetIds: [], characters: [], nodes: [], edges: [], nodeTasks: {}, selectedNodeId: 'prompt-node', selectedNodeIds: new Set(), groups: [], selectedGroupId: null, selectedEdgeIndex: null, mode: 'text-to-video', zoom: 1, panX: 0, panY: 0, drag: null, pendingPort: null, saveTimer: null, spaceDown: false, connectMode: false, canvasTool: 'move', sidebarCollapsed: false, inspectorCollapsed: false, taskPollers: new Map(), lastCompletedTask: null, scriptWorkflow: null, pendingScriptResourceId: '', titleEdit: null };
const canvasHistory = createCanvasHistory({ state, onRestore: () => {
  renderNodes(); loadNodeSettings(); scheduleSave();
} });

const emptyScriptData = (sourceNodeId = '') => ({ title: '未命名脚本', genre: '企业宣传片', duration: '60-90秒', style: '写实电影感', aspect_ratio: '16:9', resolution: '768p', source_node_id: sourceNodeId, scenes: [], resources: [], updated_at: Date.now() / 1000 });

const toast = (message) => { const el = $('#toast'); el.textContent = message; el.classList.add('show'); window.setTimeout(() => el.classList.remove('show'), 2600); };
function statusText(status, task = {}, compact = false) { if (!compact && task.status_message && ['queued','running','done'].includes(status)) return task.status_message; if (status === 'blocked' && describeTaskError(task).quotaLimited) return '模型限额暂停'; return ({ queued: '排队中', running: '生成中', done: '生成完成', failed: '生成失败', blocked: '未配置模型' }[status] || status || '待生成'); }
const videoSubmissions = new Set();
function videoSubmissionKey(nodeId, projectId = state.project?.id) { return `${projectId}:${nodeId}`; }
function videoGenerationBusy(nodeId, projectId = state.project?.id) {
  return videoSubmissions.has(videoSubmissionKey(nodeId, projectId)) ||
    state.project?.id === projectId && ['queued', 'running'].includes(state.nodeTasks[nodeId]?.status);
}
function syncVideoGenerateButton() {
  const button = $('#generateBtn'), node = selectedNode();
  if (!button) return;
  const submitting = videoSubmissions.has(videoSubmissionKey(node?.id));
  const busy = node?.type === 'video' && videoGenerationBusy(node.id);
  button.disabled = node?.type !== 'video' || state.switchingProject || busy;
  button.setAttribute('aria-busy', String(Boolean(busy)));
  const label = button.querySelector('.generate-label');
  if (label) label.textContent = submitting ? '提交中…' : busy ? statusText(state.nodeTasks[node.id]?.status) : '生成视频';
}
function taskErrorHtml(task) {
  if (!task.error) return '';
  const issue = describeTaskError(task);
  if (!issue.quotaLimited) return `<p class="task-error">${escapeHtml(issue.message)}</p>`;
  return `<div class="task-error-box"><strong>${escapeHtml(issue.title)}</strong><p>${escapeHtml(issue.message)}</p><a href="${ARK_MODEL_MANAGEMENT_URL}" target="_blank" rel="noopener noreferrer">前往火山方舟开通管理 ↗</a><p class="billing-note">${escapeHtml(issue.billingNote)}</p><details><summary>查看供应商原始错误</summary><pre>${escapeHtml(issue.raw)}</pre></details></div>`;
}
function taskRetryHtml(task) {
  if (!['failed','blocked'].includes(task.status)) return '';
  return `<button class="history-retry" data-retry-task="${escapeHtml(task.id)}">${describeTaskError(task).quotaLimited ? '查看处理方法' : '重新提交'}</button>`;
}
function showQuotaHelp(task) {
  const issue = describeTaskError(task);
  showModal(`<h2>模型用量达到上限</h2><p>${escapeHtml(issue.message)}</p><ol class="quota-steps"><li>进入火山方舟「开通管理」，确认登录的是 API Key 所属账号。</li><li>找到 ${escapeHtml(task.params?.model_name || task.params?.model || '对应模型')}，检查安心体验及模型服务状态。</li><li>按可接受的用量调整设置，确认服务恢复后再回来重新提交。</li></ol><p class="billing-note">${escapeHtml(issue.billingNote)}</p>${issue.requestId ? `<p class="request-id">请求编号：${escapeHtml(issue.requestId)}</p>` : ''}<div class="modal-actions"><a class="secondary" href="${ARK_MODEL_MANAGEMENT_URL}" target="_blank" rel="noopener noreferrer">打开开通管理 ↗</a><button class="primary" id="quotaRetry">已处理，重新提交</button></div>`);
  $('#quotaRetry').onclick = async () => { const button = $('#quotaRetry'); button.disabled = true; await retryTask(task, true); if (button.isConnected) button.disabled = false; };
}
async function api(url, options) {
  const { background = false, ...requestOptions } = options || {};
  const finish = background ? () => {} : beginRequest(url, requestOptions);
  try {
    const response = await fetch(url, withMarketSession(requestOptions, url));
    checkSessionResponse(response);
    const raw = await response.text();
    let data;
    try { data = raw ? JSON.parse(raw) : {}; } catch { throw new Error(`服务返回异常（${response.status}）`); }
    if (!response.ok) {
      const detail = Array.isArray(data.detail) ? data.detail.map(item => `${item.loc?.slice(1).join('.')}: ${item.msg}`).join('；') : data.detail;
      const error = new Error(typeof detail === 'string' ? detail : detail?.message || `请求失败（${response.status}）`);
      error.status = response.status;
      error.detail = data.detail;
      throw error;
    }
    return marketResponseData(data);
  } finally { finish(); }
}
const json = (body) => ({ method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
const session = createSessionControls({ api, beforeLogout: async () => { finishCanvasDrag(); await flushSave(); }, onError: toast });

function fillSelect(selector, items) { $(selector).innerHTML = items.map(item => `<option value="${escapeHtml(item.value ?? item)}">${escapeHtml(item.label ?? item)}</option>`).join(''); }
function selectedModel() { return state.models.find(item => item.id === $('#modelSelect')?.value) || state.models.find(item => item.is_default) || state.models[0]; }
function renderModelSelect(settings = null) { const current = state.selectedModelId || selectedModel()?.id; fillSelect('#modelSelect', state.models.map(item => ({ value: item.id, label: `${item.name}${item.api_key_set ? '' : ' · 未配置'}` }))); state.selectedModelId = state.models.some(item => item.id === current) ? current : state.models.find(item => item.is_default)?.id; if (state.selectedModelId) $('#modelSelect').value = state.selectedModelId; updateModelStatus(); applyModelCapabilities(settings); }
function updateModelStatus() {
  const model = selectedModel();
  if (!model) return;
  const credential = model.credential_source === 'market-session'
    ? model.api_key_set ? '集市会话凭据已连接' : '当前模型不支持集市接入'
    : model.api_key_set ? `API Key 已配置${model.api_key_hint ? `（${model.api_key_hint}）` : ''}` : 'API Key 未配置';
  $('#modelStatus').textContent = `${model.provider || '默认模型'} · ${credential}`;
  $('#modelStatus').className = `model-status ${model.api_key_set ? 'configured' : 'unconfigured'}`;
}
function applyModelCapabilities(settings = null) {
  const model = selectedModel();
  if (!model || !state.options) return;
  const caps = model.capabilities || {};
  const resolutions = (caps.resolutions || state.options.resolutions.map(item => item.value)).map(value => state.options.resolutions.find(item => item.value === value) || { value, label: value.toUpperCase() });
  const durations = caps.durations || state.options.durations;
  const selectedResolution = settings?.resolution ?? $('#resolutionSelect').value;
  const selectedDuration = Number(settings?.duration ?? $('#durationSelect').value);
  fillSelect('#resolutionSelect', resolutions);
  fillSelect('#durationSelect', durations.map(value => ({ value, label: `${value} 秒` })));
  $('#resolutionSelect').value = resolutions.some(item => item.value === selectedResolution) ? selectedResolution : resolutions[0]?.value;
  $('#durationSelect').value = durations.includes(selectedDuration) ? String(selectedDuration) : String(durations[0]);
}
function renderOptions() {
  const o = state.options; fillSelect('#ratioSelect', o.aspect_ratios); fillSelect('#resolutionSelect', o.resolutions); fillSelect('#durationSelect', o.durations.map(value => ({ value, label: `${value} 秒` }))); renderModelSelect();
  refreshDerivedMode(selectedNode(), false);
}
function refreshDerivedMode(node = selectedNode(), persist = false) {
  const video = node?.type === 'video' ? node : null;
  state.mode = video ? deriveVideoMode(video, state.nodes, state.edges, state.assets, state.characters) : 'text-to-video';
  const requirement = $('#assetRequirement'); if (requirement) requirement.textContent = state.mode === 'text-to-video' ? '连接图片、视频或音频节点作为参考' : `${resolveVideoAssets(video, state.nodes, state.edges, state.assets, state.characters).entries.length} 个参考素材已同步`;
  if (persist && video) captureSettings();
  if (video) renderAssets();
  return state.mode;
}
function updatePrompt() { captureSettings(); }
const removedStarterPrompts = new Set([
  '晨雾中的现代化智慧工地，塔吊缓缓运转，阳光穿过云层，施工人员佩戴安全帽，电影级镜头',
  '晨雾中的现代化智慧工地，塔吊缓缓运转，阳光穿过云层，电影级镜头'
]);
function defaultSettings() {
  return { prompt: '', negative_prompt: '模糊、抖动、人物变形、字幕、水印', mode: 'text-to-video', model_config_id: state.models.find(model => model.is_default)?.id || 'default-seedance-2.0-mini', aspect_ratio: '16:9', duration: 4, resolution: '768p', asset_ids: [] };
}
function selectedNode() { return state.nodes.find(node => node.id === state.selectedNodeId); }
function captureSettings() {
  const node = selectedNode();
  if (!node || node.type !== 'video' || state.loadingSettings) return;
  const mode = deriveVideoMode(node, state.nodes, state.edges, state.assets, state.characters);
  state.mode = mode;
  const input = resolveVideoInput(node, state.nodes, state.edges);
  const localPrompts = input.source ? (input.source.type === 'script' ? { negative_prompt: $('#negativeInput').value } : {}) : { prompt: $('#promptInput').value, negative_prompt: $('#negativeInput').value };
  const storyboard = input.source?.type === 'script' && input.scene ? scriptVideoSettings(input.source.script_data || {}, input.scene) : {};
  const inherited = { ...storyboard };
  delete inherited.aspect_ratio;
  delete inherited.resolution;
  node.settings = { ...defaultSettings(), ...node.settings, ...localPrompts, mode, model_config_id: state.selectedModelId, aspect_ratio: $('#ratioSelect').value, duration: Number($('#durationSelect').value), resolution: $('#resolutionSelect').value, ...inherited };
  $('#promptCount').textContent = $('#promptInput').value.length;
  document.querySelector(`.node[data-node="${CSS.escape(node.id)}"] .prompt-text`)?.replaceChildren(document.createTextNode(node.settings.prompt || '点击节点填写提示词'));
  scheduleSave();
}
const promptPolishRequests = new Set();
const promptPolishKey = node => `${state.project?.id || ''}:${node.id}`;
function nodeDetailHtml(node) {
  if (node && isMediaNode(node)) return mediaNodeDetail(node, nodeAsset(node), escapeHtml);
  if (!node) return '<div class="node-detail-placeholder"><div><b>选择一个节点</b><small>点击画布中的节点，这里会显示对应的设置</small></div></div>';
  if (node.type === 'prompt') {
    const settings = { ...defaultSettings(), ...node.settings };
    const polishing = promptPolishRequests.has(promptPolishKey(node));
    return `<div class="node-detail-card"><span class="node-detail-kicker">TEXT NODE · 文本输入</span><h3>${escapeHtml(node.title || '文本提示词')}</h3><div class="prompt-edit-head"><label for="detailPromptInput">文本内容</label><button type="button" class="prompt-polish" id="polishPromptBtn" ${polishing ? 'disabled' : ''}>${polishing ? '润色中…' : `${icon('sparkle','ui-icon-sm')}<span>一键润色</span>`}</button></div><textarea id="detailPromptInput" maxlength="4000">${escapeHtml(settings.prompt || '')}</textarea><div class="counter"><span id="detailPromptCount">${(settings.prompt || '').length}</span>/4000</div><label for="detailNegativeInput">负面提示词</label><textarea id="detailNegativeInput" class="short" maxlength="1000">${escapeHtml(settings.negative_prompt || '')}</textarea></div>`;
  }
  if (node.type === 'script') {
    const data = scriptDataForNode(node);
    const source = scriptSourceText(node);
    return `<div class="node-detail-card"><span class="node-detail-kicker">SCRIPT NODE · 分镜工作流</span><h3>${escapeHtml(data.title || node.title || '脚本生成器')}</h3><div class="node-detail-meta"><div><b>${data.scenes?.length || 0}</b><span>个镜头</span></div><div><b>${preparationResources(data).length}</b><span>项资源</span></div><div><b>${data.scenes?.filter(scene => scene.final_prompt)?.length || 0}</b><span>已合成提示词</span></div><div><b>${state.llm?.api_key_set ? '已配置' : '未配置'}</b><span>脚本大模型</span></div></div><label>上游文本内容</label><p class="node-detail-source">${escapeHtml(source || '')}</p><div class="node-detail-actions"><button class="primary" id="detailOpenScript">打开脚本节点</button><button class="secondary" id="detailScriptSettings">大模型设置</button></div></div>`;
  }
  if (node.type === 'character') {
    const asset = state.assets.find(item => item.id === node.assetId) || (node.assetUrl ? { url: node.assetUrl, name: node.assetName } : null);
    return `<div class="node-detail-card"><span class="node-detail-kicker">CHARACTER NODE · 角色参考</span><h3>${escapeHtml(node.characterName || node.title || '角色参考')}</h3>${asset?.url ? `<img class="node-detail-thumb" src="${escapeHtml(asset.url)}" alt="${escapeHtml(asset.name || '角色参考')}">` : '<div class="node-detail-placeholder"><div><b>暂无角色参考图</b><small>可在角色库中绑定参考素材</small></div></div>'}<label for="detailCharacterName">角色名称</label><input id="detailCharacterName" value="${escapeHtml(node.characterName || '')}" maxlength="80"><label for="detailCharacterDescription">角色设定</label><textarea id="detailCharacterDescription" rows="4">${escapeHtml(node.description || '')}</textarea></div>`;
  }
  if (node.type === 'asset') {
    const asset = state.assets.find(item => item.id === node.assetId) || (node.assetUrl ? { url: node.assetUrl, name: node.assetName } : null);
    return `<div class="node-detail-card"><span class="node-detail-kicker">ASSET NODE · 素材参考</span><h3>${escapeHtml(asset?.name || node.assetName || node.title || '素材参考')}</h3>${asset?.url ? `<img class="node-detail-thumb" src="${escapeHtml(asset.url)}" alt="${escapeHtml(asset.name || '素材参考')}">` : '<div class="node-detail-placeholder"><div><b>素材文件不可用</b><small>请从资产库重新选择</small></div></div>'}</div>`;
  }
  return `<div class="node-detail-card"><span class="node-detail-kicker">NODE · 节点信息</span><h3>${escapeHtml(node.title || '节点')}</h3><p>当前节点类型：${escapeHtml(node.type || '未知')}</p></div>`;
}
function renderNodeDetail(node) {
  const view = $('#nodeDetailView');
  if (!view) return;
  view.classList.remove('inline-composed');
  view.innerHTML = nodeDetailHtml(node);
  if (!node) return;
  if (isMediaNode(node)) {
    bindMediaNodeDetail({ node, asset: nodeAsset(node), api, toast, escapeHtml,
      onSaveAsset:(asset,inLibrary)=>assetLibrary.setLibraryMembership(asset,inLibrary),
      onPick: () => chooseNodeMedia(node), onUpload: () => uploadNodeMedia(node),
      onGenerateImage: () => generateNodeImage(node),
      onGenerateAudio: () => generateNodeAudio(node),
      onRename: title => { node.title = title; const label = document.querySelector(`[data-node="${CSS.escape(node.id)}"] .node-title b`); if (label) label.textContent = title; scheduleSave(); },
      onGenerateFromImage: asset => generateFromImage(asset, node),
      onFrameAsset: asset => { upsertAsset(asset); if (state.nodes.includes(node)) { addLibraryAsset(asset, { position: { x: node.x + 340, y: node.y + 30 } }); toast('当前画面已保存为图片素材，可连接到视频生成节点'); } }
    });
    if (['asset','prop','audio'].includes(node.type)) {
      view.classList.add('inline-composed');
      inlineNodeMedia.mount(node, view);
    } else view.classList.remove('inline-composed');
    return;
  }
  if (node.type === 'script') {
    const completion = scriptExpansionStatus(node);
    view.insertAdjacentHTML('beforeend', `<button class="secondary full node-save-library" id="expandScriptShots" ${completion.complete ? '' : 'disabled aria-disabled="true"'}>展开分镜到画布</button><small class="script-expansion-hint ${completion.complete ? 'ready' : 'pending'}">${escapeHtml(scriptExpansionHint(completion))}</small>`);
    const expandButton = $('#expandScriptShots');
    if (completion.complete) expandButton.onclick = () => expandScriptShots(node);
    else expandButton.title = scriptExpansionHint(completion);
  }
  if (node.type === 'character') {
    inlineNodeMedia.mount(node,view);
  }
  if (node.type === 'prompt') {
    const settings = { ...defaultSettings(), ...node.settings };
    const prompt = $('#detailPromptInput');
    const negative = $('#detailNegativeInput');
    const sync = () => {
      node.settings = { ...defaultSettings(), ...node.settings, prompt: prompt.value, negative_prompt: negative.value };
      $('#detailPromptCount').textContent = prompt.value.length;
      refreshDocumentFrame(node);
      scheduleSave();
    };
    prompt.oninput = sync; negative.oninput = sync;
    prompt.value = settings.prompt; negative.value = settings.negative_prompt;
    $('#polishPromptBtn').onclick = async () => {
      const requestKey = promptPolishKey(node);
      if (promptPolishRequests.has(requestKey)) return;
      const original = prompt.value;
      const source = original.trim();
      if (!source) return toast('请先填写文本内容');
      const projectId = state.project?.id;
      const button = $('#polishPromptBtn');
      promptPolishRequests.add(requestKey);
      button.disabled = true;
      button.textContent = '润色中…';
      try {
        const result = await api('/api/prompts/polish', json({ text: source }));
        const stillEditing = state.project?.id === projectId && state.nodes.includes(node)
          && state.selectedNodeId === node.id && prompt.isConnected && $('#detailPromptInput') === prompt;
        if (!stillEditing) return toast('编辑对象已切换，本次润色结果未应用');
        if (prompt.value !== original || (node.settings?.prompt || '') !== original) {
          return toast('文本已更新，本次润色结果未覆盖新内容');
        }
        if (!result.text?.trim()) return toast('大模型未返回润色结果，请重试');
        prompt.value = result.text;
        sync();
        toast('提示词已润色，正在自动保存');
      } catch (error) {
        toast(error.message);
      } finally {
        promptPolishRequests.delete(requestKey);
        // A selection change can replace this editor while the request is pending.
        const currentButton = state.project?.id === projectId && state.selectedNodeId === node.id
          ? $('#polishPromptBtn') : null;
        if (currentButton) {
          currentButton.disabled = false;
          currentButton.innerHTML = `${icon('sparkle','ui-icon-sm')}<span>一键润色</span>`;
        }
      }
    };
  }
  if (node.type === 'script') {
    $('#detailOpenScript').onclick = () => openScriptWorkflow(node.id);
    $('#detailScriptSettings').onclick = llmConfigModal;
    if (state.view==='canvas') {
      if(state.scriptWorkflow?.nodeId!==node.id) state.scriptWorkflow={nodeId:node.id,step:1,projectId:state.project?.id};
      renderScriptWorkflow();
    }
  }
  if (node.type === 'character') {
    view.insertAdjacentHTML('beforeend', '<div class="inline-composer-tools"><button type="button" id="characterPickInline">从资产库添加参考</button><button type="button" id="characterUploadInline">上传参考图</button></div>');
    $('#characterPickInline').onclick=()=>inlineNodeMedia.pick(node);
    $('#characterUploadInline').onclick=()=>uploadNodeMedia(node);
    $('#detailCharacterName').oninput = event => {
      node.characterName = event.target.value; node.title = `${event.target.value || '角色'}参考`;
      const card = document.querySelector(`.node[data-node="${CSS.escape(node.id)}"]`);
      if (card) { card.querySelector('.node-title b').textContent = node.title; card.querySelector('.character-preview b').textContent = node.characterName || '角色'; }
      scheduleSave();
    };
    $('#detailCharacterDescription').oninput = event => { node.description = event.target.value; scheduleSave(); };
  }
}
function ensureAssetNodesForVideo(video) {
  return materializeVideoAssets(video, state.nodes, state.edges, state.assets, state.characters);
}
function loadNodeSettings() {
  canvasWorkspace?.refresh();
  state.loadingSettings = true;
  const node = selectedNode();
  const editor = $('#nodeEditorFields');
  const detail = $('#nodeDetailView');
  if (!node || node.type !== 'video') {
    state.selectedAssetIds = [];
    editor.hidden = true;
    detail.hidden = false;
    renderNodeDetail(node);
    syncVideoGenerateButton();
    state.loadingSettings = false;
    return;
  }
  editor.hidden = false;
  detail.hidden = true;
  let settings = { ...defaultSettings(), ...node?.settings };
  state.selectedModelId = settings.model_config_id;
  renderModelSelect(settings);
  const input = resolveVideoInput(node, state.nodes, state.edges);
  // A video expanded from a script inherits its selected shot's settings.
  // Keep the values on the node as a snapshot as well as resolving the prompt
  // from the graph, so task submission and a reopened project use the same
  // storyboard parameters.
  if (input.source?.type === 'script' && input.scene) {
    const storyboardSettings = scriptVideoSettings(input.source.script_data || {}, input.scene);
    const inherited = {
      prompt: storyboardSettings.prompt,
      duration: storyboardSettings.duration,
      prompt_source_id: input.source.id,
      script_source_id: input.source.id,
      script_scene_index: Number(input.scene.index),
    };
    if (Object.entries(inherited).some(([key, value]) => node.settings?.[key] !== value)) {
      node.settings = { ...node.settings, ...inherited };
      scheduleSave();
    }
    settings = { ...settings, ...inherited };
  }
  $('#promptInput').value = input.prompt;
  $('#negativeInput').value = input.negative_prompt;
  $('#promptInput').readOnly = Boolean(input.source);
  $('#negativeInput').readOnly = input.source?.type === 'prompt';
  $('#promptCount').textContent = input.prompt.length;
  $('#promptSourceNote').textContent = ''; $('#promptSourceNote').hidden = true;
  $('#promptSourceNote').parentElement.hidden = !input.source;
  $('#editPromptSource').hidden = !input.source;
  $('#editPromptSource').onclick = () => {
    if (!input.source) return;
    updateSelection(input.source.id);
    if (input.source.type === 'script') openScriptWorkflow(input.source.id); else $('#detailPromptInput')?.focus();
  };
  const sourceSelect = $('#promptSourceSelect');
  sourceSelect.hidden = input.sources.length < 2;
  sourceSelect.innerHTML = input.sources.map((source, index) => `<option value="${escapeHtml(source.id)}">${index + 1}. ${escapeHtml(source.title || '文本提示词')} · ${escapeHtml((source.type === 'script' ? source.script_data?.title || '分镜脚本' : source.settings?.prompt || '空文本').slice(0, 24))}</option>`).join('');
  if (input.source) sourceSelect.value = input.source.id;
  sourceSelect.onchange = () => { node.settings.prompt_source_id = sourceSelect.value; loadNodeSettings(); scheduleSave(); };
  const sceneSelect = $('#scriptSceneSelect');
  sceneSelect.hidden = input.source?.type !== 'script';
  sceneSelect.innerHTML = input.scenes.map(scene => `<option value="${scene.index}">第 ${scene.index} 镜 · ${escapeHtml((scene.description || '未填写画面').slice(0, 26))}</option>`).join('');
  if (input.scene) sceneSelect.value = String(input.scene.index);
  sceneSelect.onchange = () => { node.settings.script_scene_index = Number(sceneSelect.value); loadNodeSettings(); scheduleSave(); };
  for (const [id, key] of [['ratioSelect','aspect_ratio'],['durationSelect','duration'],['resolutionSelect','resolution']]) {
    // Script duration stays visible for explicit validation. A saved resolution
    // must not bypass the selected model's allowed resolutions.
    const constrainedResolution = key === 'resolution' && selectedModel()?.capabilities?.resolutions?.length;
    if (input.source?.type === 'script' && !constrainedResolution && ![...$('#'+id).options].some(option => option.value === String(settings[key]))) {
      const option = document.createElement('option'); option.value = String(settings[key]);
      option.textContent = `${key === 'duration' ? `${settings[key]} 秒` : key === 'resolution' ? String(settings[key]).toUpperCase() : settings[key]} · 脚本设置`;
      $('#'+id).appendChild(option);
    }
    if ([...$('#'+id).options].some(option => option.value === String(settings[key]))) $('#'+id).value = settings[key];
  }
  refreshDerivedMode(node, false);
  renderAssets();
  const label = $('#selectedNodeLabel');
  if (label) {
    label.value = node?.title || '';
    label.disabled = !node;
    label.oninput = event => {
      if (!selectedNode() || selectedNode().id !== node?.id) return;
      node.title = event.target.value;
      updateNodeTitleInCard(node);
      scheduleSave();
    };
    label.onblur = () => normalizeNodeTitle(node);
  }
  $('#nodePanel').querySelectorAll('textarea,select,#uploadBox').forEach(control => { control.disabled = false; });
  if (input.source?.type === 'script') {
    $('#durationSelect').disabled = true;
    $('#durationSelect').title = '时长来自脚本镜头，请在脚本节点修改该镜头时长';
    for (const id of ['ratioSelect', 'resolutionSelect']) $('#'+id).title = '';
  } else for (const id of ['ratioSelect','durationSelect','resolutionSelect']) $('#'+id).title = '';
  syncVideoGenerateButton();
  state.loadingSettings = false;
}
function markSaving() { $('#saveState').textContent = '未保存'; $('#saveState').className = 'save-state saving'; }

function normaliseNodes(nodes = []) {
  return nodes.map((node, index) => {
    const type = node.type || (node.id?.includes('prompt') ? 'prompt' : 'video');
    const character = type === 'character' && state.characters.find(item => item.id === node.characterId);
    if (character) node = { ...node, characterName: character.name, description: character.description || character.role, assetId: character.asset_id || '', assetUrl: state.assets.find(item => item.id === character.asset_id)?.url || '', tone: character.tone };
    const title = node.title || (type === 'prompt' ? '文本提示词' : type === 'script' ? '脚本生成器' : '视频生成');
    const settings = { ...defaultSettings(), ...node.settings };
    if (type === 'video' && removedStarterPrompts.has(settings.prompt)) settings.prompt = '';
    return { ...node, id: node.id || crypto.randomUUID(), type, title, x: Number.isFinite(node.x) ? node.x : 80 + index * 340, y: Number.isFinite(node.y) ? node.y : 100, settings, script_data: type === 'script' ? { ...emptyScriptData(node.sourceNodeId || ''), ...(node.script_data || {}) } : node.script_data };
  });
}
function renderProject() { const p = state.project; $('#projectName').textContent = p.name; $('#canvasTitle').textContent = p.name; $('#projectNameInput').value = p.name; $('#nodeCount').textContent = state.nodes.length; $('#createdAt').textContent = p.created_at ? new Date(p.created_at * 1000).toLocaleString('zh-CN') : '—'; renderNodes(); }
let recentMenu = null;
function closeRecentMenu(restoreFocus = false) {
  if (!recentMenu) return;
  const { menu, anchor } = recentMenu;
  menu.remove();
  anchor.setAttribute('aria-expanded', 'false');
  anchor.closest('.recent-row')?.classList.remove('menu-open');
  recentMenu = null;
  if (restoreFocus && anchor.isConnected) anchor.focus();
}
function recentRows(items, kind) {
  return [...items].sort((a,b) => Number(Boolean(b.pinned))-Number(Boolean(a.pinned)) || (b.updated_at||0)-(a.updated_at||0)).slice(0,8).map(item => {
    const name = item.title || item.name || '未命名';
    const active = kind === 'project' ? state.view === 'canvas' && item.id === state.project?.id : state.view === 'agent' && state.activeConversationId === item.id;
    const glyph = kind === 'project' ? 'grid' : item.kind === 'agent' ? 'sparkle' : item.kind === 'image' ? 'imageNode' : item.kind === 'audio' ? 'audio' : 'play';
    return `<div class="recent-row ${active?'active':''}" data-recent-kind="${kind}" data-recent-id="${escapeHtml(item.id)}"><button class="recent-open ${active?'active':''}" data-${kind}="${escapeHtml(item.id)}" title="${escapeHtml(name)}"><span class="recent-kind-icon">${icon(glyph)}</span><span>${escapeHtml(name)}</span></button>${item.pinned?'<span class="recent-pin-marker" role="img" aria-label="已置顶" title="已置顶"><svg viewBox="0 0 24 24" aria-hidden="true"><g transform="rotate(45 12 12)"><path d="M8 3h8l-1 5 3 4v2h-5v7h-2v-7H6v-2l3-4-1-5Z"/></g></svg></span>':''}<button class="recent-more" type="button" aria-label="${escapeHtml(name)}的更多操作" aria-haspopup="menu" aria-expanded="false" title="更多操作"><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="5" cy="12" r="1.5"/><circle cx="12" cy="12" r="1.5"/><circle cx="19" cy="12" r="1.5"/></svg></button></div>`;
  }).join('');
}
async function updateRecentMetadata(kind, id, patch) {
  if (kind === 'project') {
    await flushSave();
    const current = state.projects.find(item => item.id === id);
    const updated = await api(`/api/projects/${encodeURIComponent(id)}/metadata`, { ...json({...patch, revision:current?.revision}), method:'PATCH' });
    if (patch.pinned !== undefined && updated.pinned !== patch.pinned) throw new Error('置顶接口尚未生效，请重启后端服务后刷新页面');
    state.projects = state.projects.map(item => item.id === id ? updated : item);
    if (state.project?.id === id) {
      state.project = {...state.project,...updated};
      $('#projectName').textContent = updated.name;
      $('#canvasTitle').textContent = updated.name;
      $('#projectNameInput').value = updated.name;
    }
    if (state.view === 'projects') renderProjectLibrary();
  } else {
    const updated = await api(`/api/conversations/${encodeURIComponent(id)}/metadata`, { ...json(patch), method:'PATCH' });
    if (patch.pinned !== undefined && updated.pinned !== patch.pinned) throw new Error('置顶接口尚未生效，请重启后端服务后刷新页面');
    state.conversations = state.conversations.map(item => item.id === id ? updated : item);
  }
  renderProjects();
}
function renameRecent(kind, item) {
  const name = item.title || item.name || '';
  showModal(`<form id="recentRenameForm"><h2>重命名</h2><label for="recentRenameInput">${kind==='project'?'画布':'对话'}名称</label><input id="recentRenameInput" maxlength="80" required value="${escapeHtml(name)}"><p id="recentRenameError" role="alert" hidden></p><div class="modal-actions"><button class="secondary" id="recentRenameCancel" type="button">取消</button><button class="primary" type="submit">保存</button></div></form>`);
  $('#recentRenameCancel').onclick = closeModal;
  $('#recentRenameInput').select();
  $('#recentRenameForm').onsubmit = async event => {
    event.preventDefault();
    const value = $('#recentRenameInput').value.trim();
    const error = $('#recentRenameError');
    if (!value) { error.textContent='名称不能为空'; error.hidden=false; return; }
    const submit = event.target.querySelector('[type="submit"]');
    submit.disabled=true;
    try { await updateRecentMetadata(kind,item.id,kind==='project'?{name:value}:{title:value}); closeModal(); toast('已重命名'); }
    catch (failure) { error.textContent=failure.message; error.hidden=false; submit.disabled=false; }
  };
}
function openRecentMenu(anchor) {
  if (recentMenu?.anchor === anchor) { closeRecentMenu(); return; }
  closeRecentMenu();
  const row = anchor.closest('.recent-row');
  const kind = row.dataset.recentKind;
  const item = (kind === 'project' ? state.projects : state.conversations).find(item => item.id === row.dataset.recentId);
  if (!item) return;
  const menu = document.createElement('div');
  menu.className = 'recent-context-menu'; menu.setAttribute('role','menu'); menu.setAttribute('aria-label','项目操作');
  const pinIcon = '<svg viewBox="0 0 24 24" aria-hidden="true"><g transform="rotate(45 12 12)"><path d="M8 3h8M9 3v5l-3 4v2h12v-2l-3-4V3M12 14v7"/></g></svg>';
  menu.innerHTML = `<button type="button" role="menuitem" data-action="pin">${pinIcon}<span>${item.pinned?'取消置顶':'置顶'}</span></button><button type="button" role="menuitem" data-action="rename">${icon('pencil')}<span>重命名</span></button><button type="button" role="menuitem" data-action="delete">${icon('trash')}<span>删除</span></button>`;
  document.body.append(menu);
  anchor.setAttribute('aria-expanded','true'); row.classList.add('menu-open');
  const box = anchor.getBoundingClientRect();
  menu.style.left = `${Math.max(8,Math.min(box.right-34,window.innerWidth-menu.offsetWidth-8))}px`;
  menu.style.top = `${Math.max(8,Math.min(box.bottom+4,window.innerHeight-menu.offsetHeight-8))}px`;
  recentMenu = {menu,anchor};
  menu.querySelector('[data-action="pin"]').onclick = async event => {
    event.currentTarget.disabled=true;
    try { await updateRecentMetadata(kind,item.id,{pinned:!item.pinned}); toast(item.pinned?'已取消置顶':'已置顶'); }
    catch (error) { toast(error.message); }
    finally { closeRecentMenu(true); }
  };
  menu.querySelector('[data-action="rename"]').onclick = () => { closeRecentMenu(); renameRecent(kind,item); };
  menu.querySelector('[data-action="delete"]').onclick = () => { closeRecentMenu(); confirmDeleteRecent(kind,item); };
  menu.onkeydown = event => {
    const entries = [...menu.querySelectorAll('button')];
    if (['ArrowDown','ArrowUp','Home','End'].includes(event.key)) {
      event.preventDefault();
      const index = entries.indexOf(document.activeElement);
      entries[event.key==='Home'?0:event.key==='End'?entries.length-1:(index+(event.key==='ArrowDown'?1:-1)+entries.length)%entries.length].focus();
    }
  };
  menu.querySelector('button').focus({preventScroll:true});
}
document.addEventListener('pointerdown', event => {
  if (recentMenu && !recentMenu.menu.contains(event.target) && !recentMenu.anchor.contains(event.target)) closeRecentMenu();
});
document.addEventListener('keydown', event => { if (event.key === 'Escape' && recentMenu) { event.preventDefault(); closeRecentMenu(true); } });
window.addEventListener('resize', () => closeRecentMenu());
document.addEventListener('scroll', event => { if (recentMenu && !recentMenu.menu.contains(event.target)) closeRecentMenu(); }, true);
function renderProjects() {
  closeRecentMenu();
  $('#projectList').innerHTML = `<div class="recent-section-label">对话创作</div>${recentRows(state.conversations||[],'conversation')||'<p class="recent-empty">暂无对话</p>'}<div class="recent-section-label">画布创作</div>${recentRows(state.projects,'project')||'<p class="recent-empty">暂无画布</p>'}`;
  $('#projectList').querySelectorAll('[data-project]').forEach(el => el.onclick = () => selectProject(el.dataset.project).catch(error => toast(error.message)));
  $('#projectList').querySelectorAll('[data-conversation]').forEach(el => el.onclick = () => openConversation(el.dataset.conversation).catch(error => toast(error.message)));
  $('#projectList').querySelectorAll('.recent-more').forEach(el => el.onclick = () => openRecentMenu(el));
}
async function refreshConversations(){state.conversations=await api('/api/conversations');renderProjects();}
let conversationOpenRevision=0;
async function openConversation(id){ return withLoading(() => loadConversation(id), '正在打开对话…'); }
async function loadConversation(id){
  const revision=++conversationOpenRevision,current=()=>revision===conversationOpenRevision;
  await flushSave();if(!current())return;
  const item=(state.conversations||[]).find(item=>item.id===id)||await api(`/api/conversations/${encodeURIComponent(id)}`);
  if(!current())return;
  if(item.kind==='agent'&&item.run_id){
    const snapshot=await api(`/api/agent/runs/${encodeURIComponent(item.run_id)}`);if(!current())return;
    homeComposer.select('agent');await agentChat.openRun(item.run_id,snapshot);
  }else await homeComposer.openConversation(id,current);
  if(!current())return;
  state.activeConversationId=id;conversationResume.remember(state.user,id);homeHub?.showConversation();showView('agent',true);history.replaceState(null,'',`?conversation=${encodeURIComponent(id)}`);
}
function renderProjectLibrary(projects = state.projects, trashed = false) { projectLibrary.render(projects, trashed); }
async function duplicateProject(id) { try { await flushSave(); const copy = await api(`/api/projects/${id}/duplicate`, { method: 'POST' }); state.projects.unshift(copy); renderProjects(); renderProjectLibrary(); toast(`已复制项目：${copy.name}`); } catch (error) { toast(error.message); } }
async function deleteProject(id) {
  const project=state.projects.find(item=>item.id===id);if(!project)return false;
  const gallery=state.view==='projects';
  try {
    await flushSave();await api(`/api/projects/${id}`,{method:'DELETE'});
    state.projects=state.projects.filter(item=>item.id!==id);
    if(state.project?.id===id){
      state.project=null;
      if(!gallery&&state.projects[0])await selectProject(state.projects[0].id);
      else {state.nodes=[];state.edges=[];state.groups=[];state.selectedNodeId=null;state.selectedNodeIds.clear();if(!gallery)showView('projects');}
    }
    renderProjects();renderProjectLibrary();toast(`项目「${project.name}」已移入回收站`);return true;
  }catch(error){toast(error.message);return false;}
}
function confirmDeleteRecent(kind,item) {
  const name=item.title||item.name||'未命名项目';
  showModal(`<h2>确认删除「${escapeHtml(name)}」</h2><p class="recent-delete-description">此操作无法撤回，删除后，仍可在「资产」中找回已生成的内容，确认删除么？</p><p id="recentDeleteError" role="alert" hidden></p><div class="modal-actions"><button type="button" class="secondary" id="recentDeleteCancel">取消</button><button type="button" class="primary" id="recentDeleteConfirm">删除</button></div>`,'recent-delete-modal');
  $('#recentDeleteCancel').onclick=closeModal;
  $('#recentDeleteConfirm').onclick=async()=>{
    const confirm=$('#recentDeleteConfirm'),cancel=$('#recentDeleteCancel');
    confirm.disabled=true;cancel.disabled=true;$('#modalClose').disabled=true;
    try{
      await flushSave();
      await api(`/api/recent-items/${kind}/${encodeURIComponent(item.id)}`,{method:'DELETE'});
      const ownsProject=kind==='project'&&state.project?.id===item.id;
      const active=kind==='project'?ownsProject&&state.view==='canvas':state.view==='agent'&&state.activeConversationId===item.id;
      if(kind==='project'){
        state.projects=state.projects.filter(project=>project.id!==item.id);
        if(ownsProject){state.project=null;state.nodes=[];state.edges=[];state.groups=[];state.selectedNodeId=null;state.selectedNodeIds.clear();state.dirty=false;}
      }else state.conversations=state.conversations.filter(conversation=>conversation.id!==item.id);
      closeModal();renderProjects();
      if(active){state.activeConversationId=null;showView('home');history.replaceState(null,'','?view=home');}
      else if(state.view==='projects')renderProjectLibrary();
      toast('已删除，生成内容仍保留在资产中');
    }catch(error){const message=$('#recentDeleteError');if(message){message.hidden=false;message.textContent=error.message;}confirm.disabled=false;cancel.disabled=false;}
    finally{$('#modalClose').disabled=false;}
  };
  $('#recentDeleteCancel').focus();
}
function confirmDeleteProject(id) {
  const project = state.projects.find(item => item.id === id);
  if (!project) return;
  showModal(`<div class="delete-confirm"><div class="delete-confirm-heading"><span class="delete-confirm-icon">${icon('trash')}</span><div><h2 id="deleteConfirmTitle">移入回收站？</h2><p>项目移除后，可在回收站中恢复。</p></div></div><div class="delete-confirm-item">${icon('folder')}<span>${escapeHtml(project.name)}</span></div><div class="modal-actions"><button type="button" class="secondary" id="cancelProjectTrash">取消</button><button type="button" class="danger-fill" id="confirmProjectTrash">${icon('trash')}<span>移入回收站</span></button></div></div>`, 'delete-confirm-modal');
  $('#cancelProjectTrash').onclick = closeModal;
  $('#confirmProjectTrash').onclick = async () => { const button = $('#confirmProjectTrash'); button.disabled = true; if (await deleteProject(id)) closeModal(); else button.disabled = false; };
  $('#cancelProjectTrash').focus();
}
async function restoreProject(id) { try { const restored = await api(`/api/projects/${id}/restore`, { method: 'POST' }); state.projects.unshift(restored); renderProjects(); const trashed = await api('/api/projects?trashed=true'); renderProjectLibrary(trashed, true); toast(`项目「${restored.name}」已恢复`); } catch (error) { toast(error.message); } }
function confirmPurgeProject(id, name) {
  if (!id) return;
  showModal(`<div class="delete-confirm"><div class="delete-confirm-heading"><span class="delete-confirm-icon">${icon('trash')}</span><div><h2 id="deleteConfirmTitle">永久删除项目？</h2><p>删除后，画布和生成记录将无法恢复。</p></div></div><div class="delete-confirm-item">${icon('folder')}<span>${escapeHtml(name || '未命名项目')}</span></div><div class="modal-actions"><button type="button" class="secondary" id="cancelProjectPurge">取消</button><button type="button" class="danger-fill" id="confirmProjectPurge">${icon('trash')}<span>永久删除</span></button></div></div>`, 'delete-confirm-modal');
  $('#cancelProjectPurge').onclick = closeModal;
  $('#cancelProjectPurge').focus();
  $('#confirmProjectPurge').onclick = async () => {
    const button = $('#confirmProjectPurge');
    button.disabled = true;
    try {
      await api(`/api/projects/${id}`, { method: 'DELETE' });
      if (state.project?.id === id) {
        state.project = null;
        state.projects = state.projects.filter(item => item.id !== id);
        if (state.projects[0]) await selectProject(state.projects[0].id);
      }
      renderProjectLibrary(await api('/api/projects?trashed=true'), true);
      closeModal();
      toast(`项目「${name || '未命名项目'}」已永久删除`);
    } catch (error) {
      toast(error.message);
      button.disabled = false;
    }
  };
}

async function flushSave() {
  finishNodeTitleEdit(true);
  clearTimeout(state.saveTimer);
  if (state.saveInFlight) await state.saveInFlight;
  while (state.project && state.dirty) await saveProject(true);
}
async function selectProject(id) { return withLoading(() => loadProject(id), '正在打开画布…'); }
async function loadProject(id) {
  if (state.switchingProject) return;
  if(state.project?.id!==id)canvasAgent?.projectChanged();
  state.switchingProject = true;
  syncVideoGenerateButton();
  try {
    await flushSave();
    const project = await api(`/api/projects/${id}`);
    canvasConnections?.reset();
    state.project = project;
    if(!document.body.classList.contains('agent-embedded'))history.replaceState(null,'',`?project=${encodeURIComponent(project.id)}`);
    state.nodes = normaliseNodes(project.nodes);
    const starterPromptsCleared = state.nodes.some((node,index) => node.type === 'video' && removedStarterPrompts.has(project.nodes[index]?.settings?.prompt));
    state.groups = normalizeGroups(project.groups || [], state.nodes);
    canvasGroups?.reset();
    state.edges = (project.edges || []).filter(edge => state.nodes.some(n => n.id === edge.from) && state.nodes.some(n => n.id === edge.to));
    state.pendingPort = null; state.selectedEdgeIndex = null;
    state.nodeTasks = {};
    state.lastCompletedTask = null;
    state.selectedNodeId = state.nodes.find(n => n.type === 'video')?.id || state.nodes[0]?.id;
    state.selectedNodeIds = state.selectedNodeId ? new Set([state.selectedNodeId]) : new Set();
    state.dirty = false; state.changeVersion = 0;
    const referencesMigrated = state.nodes.filter(node => node.type === 'video').map(ensureAssetNodesForVideo).some(Boolean);
    const scriptResourcesMigrated = syncScriptResourcesToCanvas();
    state.zoom = Number.isFinite(project.viewport?.zoom) ? project.viewport.zoom : 1;
    state.panX = Number.isFinite(project.viewport?.x) ? project.viewport.x : 0;
    state.panY = Number.isFinite(project.viewport?.y) ? project.viewport.y : 0;
    closeCanvasPanel();
    renderProject(); renderProjects(); loadNodeSettings(); showView('canvas');
    if (!Number.isFinite(project.viewport?.zoom) && state.nodes.some(n => n.agent_run_id)) fitCanvas();
    $('#saveState').textContent = '已保存'; $('#saveState').className = 'save-state';
    if (referencesMigrated || scriptResourcesMigrated || starterPromptsCleared) scheduleSave();
    await loadHistory();
    Object.entries(state.nodeTasks).forEach(([nodeId, task]) => updateTaskNode(task, nodeId));
  } finally { state.switchingProject = false; syncVideoGenerateButton(); canvasHistory.reset(); }
}
function projectPayload() {
  return { name: $('#projectNameInput').value.trim() || state.project.name, nodes: structuredClone(state.nodes), groups: structuredClone(normalizeGroups(state.groups,state.nodes)), edges: structuredClone(state.edges), selected_asset_ids: [], viewport: { x: state.panX, y: state.panY, zoom: state.zoom }, revision: state.project.revision || 0 };
}
async function saveProject(quiet = false) {
  if (!quiet) finishNodeTitleEdit(true);
  clearTimeout(state.saveTimer);
  if (!state.project) return;
  if (state.saveInFlight) { await state.saveInFlight; if (!state.dirty) return; }
  const id = state.project.id, version = state.changeVersion || 0, payload = projectPayload();
  $('#saveState').textContent = '保存中…';
  const pending = api(`/api/projects/${id}`, { ...json(payload), method: 'PUT' });
  state.saveInFlight = pending;
  try {
    const saved = await pending;
    state.projects = state.projects.map(item => item.id === id ? saved : item);
    if (state.project?.id === id) {
      state.project.revision = saved.revision;
      state.project.updated_at = saved.updated_at;
      state.dirty = version !== state.changeVersion;
      if (!state.dirty) {
        state.project.name = saved.name;
        $('#projectName').textContent = saved.name; $('#canvasTitle').textContent = saved.name;
        $('#saveState').textContent = '已保存'; $('#saveState').className = 'save-state';
      } else markSaving();
      renderProjects();
    }
    if (!quiet) toast('项目已保存');
  } catch (error) {
    state.dirty = true;
    $('#saveState').textContent = '保存失败，点击重试'; $('#saveState').className = 'save-state error';
    toast(error.message);
    throw error;
  } finally { if (state.saveInFlight === pending) state.saveInFlight = null; }
}
function scheduleSave() {
  if (!state.project || state.agentHandoff) return;
  canvasHistory.record();
  state.dirty = true; state.changeVersion = (state.changeVersion || 0) + 1;
  markSaving(); clearTimeout(state.saveTimer);
  state.saveTimer = window.setTimeout(() => saveProject(true).catch(() => {}), 700);
}
function videoPreviewUrl(node, task) {
  if (task) return task.status === 'done' ? task.video_url || '' : '';
  const asset = node && nodeAsset(node);
  return asset?.kind === 'video' ? asset.url || '' : '';
}
function videoPreviewBody(node, task) {
  const url = videoPreviewUrl(node, task);
  if (url) return `<video class="frame-media" controls preload="metadata" playsinline${task?.id ? ` data-video-task="${escapeHtml(task.id)}"` : ''} src="${escapeHtml(url)}"></video>`;
  const failed = task?.status === 'failed' || task?.video_cache_status === 'expired';
  const label = task?.video_cache_status === 'expired' ? '视频链接已失效，请重新生成' : task?.status === 'failed' ? describeTaskError(task).title : task ? statusText(task.status, task, true) : '等待生成预览';
  return `<div class="play">${icon(failed ? 'close' : 'play')}</div><span>${escapeHtml(label)}</span>`;
}
function videoStatusClass(task, node) {
  if (!task?.status) return videoPreviewUrl(node, task) ? 'done' : '';
  if (task.status === 'done') return 'done';
  if (task.status === 'failed' || task.status === 'blocked') return 'failed';
  if (task.status === 'queued' || task.status === 'running') return 'running';
  return '';
}
function videoMetaHtml(node, task) {
  const status = task?.status || (videoPreviewUrl(node, task) ? 'done' : undefined);
  return `<div class="node-meta"><span>${escapeHtml(statusText(status, task || {}, true))}</span></div>`;
}
function videoPlaybackErrorText(task) {
  return task?.video_cache_status === 'fallback'
    ? '视频已生成，但本地缓存失败，请立即下载或重新生成'
    : '视频链接已失效，请重新生成';
}
function bindVideoPlayback(video, task) {
  if (!video || video.dataset.playbackBound === 'true') return;
  video.dataset.playbackBound = 'true';
  video.setAttribute('playsinline', '');
  video.addEventListener('error', () => {
    const host = video.closest('.video-preview, .library-thumb, .preview-media');
    if (!host || host.dataset.playbackFailed === 'true') return;
    host.dataset.playbackFailed = 'true';
    if (host.classList.contains('video-preview')) {
      host.innerHTML = `<div class="play">${icon('close')}</div><span>${escapeHtml(videoPlaybackErrorText(task))}</span>`;
    } else {
      host.innerHTML = `<span class="video-playback-error">${escapeHtml(videoPlaybackErrorText(task))}</span>`;
    }
    // Older tasks may still have a valid provider URL from before local
    // caching was introduced.  Repair those silently once; an expired URL
    // stays on the localized error state above instead of looping forever.
    if (!task?.id || task.video_cache_status || String(task.video_url || '').startsWith('/uploads/')) return;
    api(`/api/tasks/${encodeURIComponent(task.id)}/cache-video`, { method: 'POST' }).then(repaired => {
      if (!repaired?.video_url || !host.isConnected || repaired.video_url === task.video_url) return;
      host.dataset.playbackFailed = 'false';
      const modal = host.classList.contains('preview-media');
      host.innerHTML = modal
        ? `<video class="modal-video" controls autoplay playsinline src="${escapeHtml(repaired.video_url)}"></video>`
        : `<video class="frame-media" controls preload="metadata" playsinline src="${escapeHtml(repaired.video_url)}"></video>`;
      const repairedVideo = host.querySelector('video');
      bindVideoPlayback(repairedVideo, repaired);
      const frame = host.closest('.node.visual-frame');
      if (frame && repairedVideo) watchFrameMedia(state.nodes.find(item => item.id === frame.dataset.node), frame, repairedVideo);
    }).catch(() => {});
  });
}
function normalizeNodeTitle(node) {
  if (!node) return;
  const fallback = ({video:'视频生成',prompt:'文本素材',script:'脚本素材',asset:'图片素材',prop:'道具素材','video-asset':'视频素材',audio:'音频素材',character:'角色参考'})[node.type] || '节点';
  const next = String(node.title || '').trim() || fallback;
  const changed = node.title !== next;
  node.title = next;
  updateNodeTitleInCard(node);
  const field = $('#selectedNodeLabel');
  if (field && selectedNode()?.id === node.id) field.value = next;
  if (changed) scheduleSave();
}
function updateNodeTitleInCard(node) {
  const label = document.querySelector(`.node[data-node="${CSS.escape(node.id)}"] .node-title b`);
  if (label) label.textContent = node.title || '';
}
function finishNodeTitleEdit(commit = true) {
  const edit = state.titleEdit;
  if (!edit) return;
  const input = edit.input;
  state.titleEdit = null;
  if (commit) {
    const previousTitle = edit.node.title;
    edit.node.title = input.value;
    normalizeNodeTitle(edit.node);
    if (edit.node.title !== previousTitle) scheduleSave();
  }
  if (input.isConnected) {
    const label = document.createElement('b');
    label.textContent = edit.node.title || edit.previousLabel;
    input.replaceWith(label);
  }
  canvasWorkspace?.refresh();
}
function beginNodeTitleEdit(node, event) {
  if (!node || state.canvasTool === 'hand' || state.spaceDown) return;
  event.preventDefault();
  event.stopPropagation();
  finishNodeTitleEdit(true);
  const label = document.querySelector(`.node[data-node="${CSS.escape(node.id)}"] .node-title b`);
  if (!label) return;
  const input = document.createElement('input');
  input.className = 'node-title-editor';
  input.type = 'text'; input.maxLength = 80; input.value = node.title || label.textContent;
  input.setAttribute('aria-label', '编辑节点名称');
  label.replaceWith(input);
  state.titleEdit = { node, input, previousLabel: label.textContent };
  input.onpointerdown = click => click.stopPropagation();
  input.onclick = click => click.stopPropagation();
  input.onkeydown = keyEvent => {
    keyEvent.stopPropagation();
    if (keyEvent.isComposing || keyEvent.keyCode === 229) return;
    if (keyEvent.key === 'Enter') { keyEvent.preventDefault(); input.blur(); }
    if (keyEvent.key === 'Escape') { keyEvent.preventDefault(); finishNodeTitleEdit(false); }
  };
  input.onblur = () => { if (state.titleEdit?.input === input) finishNodeTitleEdit(true); };
  input.focus(); input.select();
}

function isVisualFrame(node) {
  return ['asset', 'prop', 'video-asset', 'video', 'prompt', 'script', 'audio'].includes(node?.type);
}
function frameNaturalSize(node) {
  const width = Number(node?.mediaWidth), height = Number(node?.mediaHeight);
  if (!Number.isFinite(width) || !Number.isFinite(height)) return null;
  const w = Math.round(width), h = Math.round(height);
  if (w < 1 || h < 1 || w > 20000 || h > 20000) return null;
  return { w, h };
}
function frameBox(node) {
  if (node?.type === 'audio') return { width: 420, height: nodeAsset(node)?.url || node.assetUrl ? 176 : 250, filled: false, naturalW: 0, naturalH: 0 };
  if (['prompt', 'script'].includes(node?.type)) return { width: 336, height: 196, filled: false, naturalW: 0, naturalH: 0 };
  const natural = frameNaturalSize(node);
  if (!natural) return { width: 336, height: 196, filled: false, naturalW: 0, naturalH: 0 };
  const ratio = Math.min(2.45, Math.max(1 / 2.45, natural.w / natural.h));
  const width = ratio >= 1 ? 300 : Math.max(128, Math.round(300 * ratio));
  const height = ratio >= 1 ? Math.max(128, Math.round(300 / ratio)) : 300;
  return { width, height, filled: true, naturalW: natural.w, naturalH: natural.h };
}
function paintFrame(element, node) {
  if (!element) return;
  const box = frameBox(node);
  element.style.setProperty('--frame-w', box.width + 'px');
  element.style.setProperty('--frame-h', box.height + 'px');
  const dims = element.querySelector('.frame-dims');
  if (dims) dims.textContent = box.filled ? box.naturalW + ' \u00d7 ' + box.naturalH : '';
}
function rememberFrameSize(node, element, width, height) {
  const w = Math.round(Number(width)), h = Math.round(Number(height));
  if (!node || w < 1 || h < 1) return;
  if (node.mediaWidth === w && node.mediaHeight === h) return;
  node.mediaWidth = w;
  node.mediaHeight = h;
  paintFrame(element, node);
  // Agent previews derive these dimensions from the bound file. Writing them
  // back races the controller's next node update and serves no editing purpose.
  if (!node.agent_run_id) scheduleSave();
}
function clearFrameSize(node, element) {
  if (!node || (node.mediaWidth == null && node.mediaHeight == null)) return;
  delete node.mediaWidth;
  delete node.mediaHeight;
  paintFrame(element, node);
  if (!node.agent_run_id) scheduleSave();
}
function watchFrameMedia(node, element, media) {
  if (!node || !element || !media || media.dataset.frameWatch === '1') return;
  media.dataset.frameWatch = '1';
  if (media.tagName === 'VIDEO') {
    // Native video surfaces consume pointer gestures. Keep their bottom control
    // strip accessible and give the picture itself the same drag target as images.
    const surface = document.createElement('div');
    surface.className = 'video-drag-surface';
    surface.title = '拖动视频画面移动节点；底部控制条可播放视频';
    surface.setAttribute('aria-hidden', 'true');
    media.after(surface);
  }
  const apply = () => rememberFrameSize(node, element, media.naturalWidth || media.videoWidth, media.naturalHeight || media.videoHeight);
  if ((media.tagName === 'IMG' && media.complete && media.naturalWidth) || media.videoWidth) apply();
  media.addEventListener(media.tagName === 'IMG' ? 'load' : 'loadedmetadata', apply);
}
function bindFrameMedia(node, element) {
  element?.querySelectorAll('.frame-media').forEach(media => watchFrameMedia(node, element, media));
}
function visualFrameTemplate(node, view) {
  const box = frameBox(node);
  const dims = box.filled ? box.naturalW + ' \u00d7 ' + box.naturalH : '';
  const port = direction => `<div class="port ${direction}" data-port="${direction}" data-node-port="${escapeHtml(node.id)}" aria-label="${direction === 'in' ? '\u8f93\u5165\u7aef\u53e3' : '\u8f93\u51fa\u7aef\u53e3'}" title="${direction === 'in' ? '\u8f93\u5165\u7aef\u53e3' : '\u8f93\u51fa\u7aef\u53e3'}"><span class="port-mark" aria-hidden="true">+</span></div>`;
  const status = node.type === 'video' ? `<span class="status-dot ${videoStatusClass(view.task, node)}"></span>${videoMetaHtml(node, view.task)}` : '';
  return `<article class="node visual-frame ${view.classes} ${view.selected ? 'selected' : ''} ${state.pendingPort === node.id ? 'edge-source' : ''}" data-node="${escapeHtml(node.id)}" data-node-type="${escapeHtml(node.type)}" style="left:${node.x}px;top:${node.y}px;--frame-w:${box.width}px;--frame-h:${box.height}px" tabindex="0"><div class="frame-head node-title drag-handle"><span class="frame-name"><span class="node-icon frame-icon">${icon(view.nodeIcon)}</span><b>${escapeHtml(view.title)}</b></span><span class="frame-dims">${dims}</span>${status}<button class="node-menu" aria-label="\u5220\u9664\u8282\u70b9" data-delete-node="${escapeHtml(node.id)}" title="\u5220\u9664\u8282\u70b9">${icon('close','ui-icon-sm')}</button></div><div class="frame-stage"><div class="frame-canvas">${view.body}</div>${view.input ? port('in') : ''}${port('out')}</div></article>`;
}

function openTextNode(id) {
  updateSelection(id);
  setRailState('inspector', false);
  $('#detailPromptInput')?.focus();
}
function refreshDocumentFrame(node) {
  const canvas = document.querySelector(`.node[data-node="${CSS.escape(node.id)}"] .frame-canvas`);
  if (!canvas) return;
  canvas.innerHTML = documentFrameBody(node, { ...emptyScriptData(), ...node.script_data });
  const edit = canvas.querySelector('[data-edit-text]');
  if (edit) edit.onclick = event => { event.stopPropagation(); openTextNode(node.id); };
}
function documentFrameBody(node, data) {
  const prompt = node.type === 'prompt';
  const text = node.settings?.prompt?.trim() || '';
  const scenes = data.scenes || [];
  const filled = prompt ? Boolean(text) : scenes.length > 0;
  const action = prompt ? `data-edit-text="${escapeHtml(node.id)}"` : `data-open-script="${escapeHtml(node.id)}"`;
  const actionLabel = prompt ? (filled ? '编辑文本' : '编写文本') : (filled ? '打开分镜脚本' : '创建分镜脚本');
  const button = `<button type="button" ${action}>${icon(prompt ? 'pencil' : 'script', 'ui-icon-sm')}<span>${actionLabel}</span></button>`;
  if (!filled) return `<div class="frame-empty document-frame-empty"><span class="frame-placeholder document-placeholder" aria-hidden="true">${icon(prompt ? 'text' : 'script')}</span><div class="frame-try"><span>尝试：</span>${button}<small>${prompt ? '写下灵感，连接脚本或视频节点' : '导入 Excel 或根据文本拆分分镜'}</small></div></div>`;
  const preview = prompt ? `<p class="document-excerpt">${escapeHtml(text)}</p>` : `<strong class="document-heading">${escapeHtml(data.title || '分镜脚本')}</strong><p class="document-excerpt">${escapeHtml(scenes.slice(0, 3).map((scene, index) => `${index + 1}. ${scene.description || '待填写画面描述'}`).join('\n'))}</p>`;
  const summary = prompt ? `${text.length} 字${node.sourceAssetId ? ' · 来自资产库' : ''}` : `${scenes.length} 个镜头 · ${preparationResources(data).length} 项资源`;
  return `<div class="document-frame-content">${preview}<div class="document-frame-footer"><span>${summary}</span><div class="frame-try">${button}</div></div></div>`;
}

function nodeTemplate(node) {
  const prompt = node.type === 'prompt', character = node.type === 'character', script = node.type === 'script', media = isMediaNode(node);
  const title = node.title || ({prompt:'文本素材',script:'脚本素材',asset:'图片素材',prop:'道具素材','video-asset':'视频素材',audio:'音频素材',character:'角色参考',video:'视频生成'}[node.type]);
  const input = node.type === 'audio' || ['asset', 'prop'].includes(node.type) || (!prompt && !character && !media);
  const task = state.nodeTasks[node.id];
  const data = { ...emptyScriptData(), ...node.script_data };
  let body;
  if (media) body = mediaNodeBody(node, nodeAsset(node), escapeHtml);
  else if (prompt || script) body = documentFrameBody(node, data);
  else if (character) { const asset = nodeAsset(node); body = `<div class="character-preview ${escapeHtml(node.tone || 'blue')}">${asset?.url ? `<img class="character-photo" src="${escapeHtml(asset.url)}" alt="角色参考图">` : `<span class="character-avatar">${icon('character')}</span>`}<div><b>${escapeHtml(node.characterName || title)}</b><small>${asset?.url ? '角色形象已绑定' : '尚未绑定角色形象'}</small></div></div><div class="node-meta"><span>角色参考</span><span>跨画布复用</span></div>`; }
  else body = `<div class="video-preview">${videoPreviewBody(node, task)}</div>`;
  const kind = node.type === 'asset' ? 'image' : node.type === 'prop' ? 'prop' : node.type === 'video-asset' ? 'video' : node.type;
  const classes = prompt ? 'prompt-node' : script ? 'script-node' : character ? 'character-node' : media ? `asset-node media-node media-${kind}` : 'video-node';
  const nodeIcon = ({prompt:'text',script:'script',asset:'imageNode',prop:'prop','video-asset':'play',audio:'audio',character:'character',video:'play'})[node.type] || 'diamond';
  const portHtml = direction => `<div class="port ${direction}" data-port="${direction}" data-node-port="${escapeHtml(node.id)}" aria-label="${direction === 'in' ? '输入端口' : '输出端口'}" title="${direction === 'in' ? '输入端口' : '输出端口'}"><span class="port-mark" aria-hidden="true">+</span></div>`;
  const selected = !state.selectedGroupId && (state.selectedNodeIds?.has(node.id) || node.id === state.selectedNodeId);
  if (isVisualFrame(node)) return visualFrameTemplate(node, { body, title, nodeIcon, input, task, classes, selected });
  return `<article class="node ${classes} ${selected ? 'selected' : ''} ${state.pendingPort === node.id ? 'edge-source' : ''}" data-node="${escapeHtml(node.id)}" data-node-type="${escapeHtml(node.type)}" style="left:${node.x}px;top:${node.y}px" tabindex="0"><div class="node-title drag-handle"><span class="node-icon ${prompt || script ? 'purple' : character ? 'gold' : media ? 'pink' : 'cyan'}">${icon(nodeIcon)}</span><b>${escapeHtml(title)}</b><span class="status-dot ${videoStatusClass(task)}"></span><button class="node-menu" aria-label="删除节点" data-delete-node="${escapeHtml(node.id)}" title="删除节点">${icon('close','ui-icon-sm')}</button></div>${body}${input ? portHtml('in') : ''}${portHtml('out')}</article>`;
}
let edgeResizeFrame;
const nodeSizeObserver = new ResizeObserver(() => {
  cancelAnimationFrame(edgeResizeFrame);
  edgeResizeFrame = requestAnimationFrame(renderEdges);
});
function renderNodes() {
  canvasWorkspace?.refresh();
  finishNodeTitleEdit(true);
  nodeSizeObserver.disconnect();
  const stage = $('#canvasStage');
  const previousIds = new Set([...stage.querySelectorAll('.node')].map(node => node.dataset.node));
  stage.innerHTML = `<svg class="links" aria-label="节点连线"></svg>${state.nodes.map(nodeTemplate).join('')}`;
  stage.querySelectorAll('.node').forEach(node => { if (!previousIds.has(node.dataset.node)) node.classList.add('node-entering'); });
  setZoom(state.zoom); renderEdges(); bindNodeEvents();
  stage.querySelectorAll('.video-preview video').forEach(video => bindVideoPlayback(video, state.nodeTasks[video.closest('.node')?.dataset.node]));
  stage.querySelectorAll('.node.visual-frame').forEach(element => bindFrameMedia(state.nodes.find(item => item.id === element.dataset.node), element));
  stage.querySelectorAll('.node').forEach(node => nodeSizeObserver.observe(node));
  $('#nodeCount').textContent = state.nodes.length;
}
function portPosition(nodeId, direction) {
  const node = document.querySelector(`.node[data-node="${CSS.escape(nodeId)}"]`);
  if (!node || !node.querySelector(`[data-port="${direction}"]`)) return null;
  // The add/connect buttons sit outside the card. Wires attach to the media
  // frame itself so hover hit areas never move the wire or leave a gap.
  const body = node.querySelector('.frame-stage,.character-preview,.asset-preview') || node;
  const rect = body.getBoundingClientRect(), stage = $('#canvasStage').getBoundingClientRect();
  return { x: ((direction==='out'?rect.right:rect.left) - stage.left) / state.zoom, y: (rect.top + rect.height / 2 - stage.top) / state.zoom };
}
function wirePath(from, to) {
  const curve = Math.max(60, Math.min(240, Math.abs(to.x - from.x) * .45));
  // Output tangents always point right; input tangents always arrive from the left.
  return `M ${from.x} ${from.y} C ${from.x + curve} ${from.y}, ${to.x - curve} ${to.y}, ${to.x} ${to.y}`;
}
function renderEdges() {
  canvasGroups?.refresh();
  const svg = $('#canvasStage .links');
  if (!svg) return;
  const focusedEdge = svg.contains(document.activeElement) ? document.activeElement.closest('[data-edge]')?.dataset.edge : null;
  const paths = state.edges.map((edge, index) => {
    if (!edge?.from || !edge?.to) return '';
    const from = portPosition(edge.from, 'out'), to = portPosition(edge.to, 'in');
    if (!from || !to) return '';
    const selected = state.selectedEdgeIndex === index ? ' edge-selected' : '';
    const related = [edge.from,edge.to].some(id=>state.selectedNodeIds?.has(id)||state.selectedNodeId===id) ? ' edge-related' : '';
    const d = wirePath(from, to);
    const flow = edge.relation === 'agent-flow';
    const label = flow ? `${edge.label || '流程关联'}：${state.nodes.find(n=>n.id===edge.from)?.title || ''} → ${state.nodes.find(n=>n.id===edge.to)?.title || ''}` : `生成输入 ${index + 1}`;
    // One long, progressively brighter trail travels on the exact wire stroke.
    // All layers share its width; no wide glow or blur spills beyond the line.
    const segments=Array.from({length:10},(_,i)=>{
      const offset=i*13.5, length=135-offset;
      return `<path d="${d}" pathLength="300" stroke-dasharray="0 ${offset} ${length} 165" style="stroke-opacity:${.08+i*.018}"/>`;
    }).join('');
    return `<g class="edge${selected}${related}" data-edge="${index}" data-edge-from="${escapeHtml(edge.from)}" data-edge-to="${escapeHtml(edge.to)}" role="button" tabindex="0" aria-label="${escapeHtml(label)}，选中后按 Delete 删除"><title>${escapeHtml(label)}</title><path class="edge-hit" d="${d}"></path><path class="edge-line" d="${d}"></path><g class="edge-flow" aria-hidden="true">${segments}</g></g>`;
  }).join('');
  const start = state.pendingPort && portPosition(state.pendingPort, 'out');
  const draft = start && state.wireCursor ? `<path class="edge-draft" d="${wirePath(start, state.wireCursor)}"></path>` : '';
  svg.innerHTML = `${paths}${draft}`;
  svg.querySelectorAll('[data-edge]').forEach(edge => {
    const select = event => { event.stopPropagation(); updateSelection(null); state.selectedEdgeIndex = Number(edge.dataset.edge); renderEdges(); toast('已选中连线，按 Delete 可删除'); };
    edge.onclick = select;
    edge.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); select(event); } };
  });
  if (focusedEdge != null) svg.querySelector(`[data-edge="${CSS.escape(focusedEdge)}"]`)?.focus({ preventScroll: true });
}
function updateSelection(id) {
  state.selectedGroupId = null;
  canvasWorkspace?.onSelect();
  state.selectedEdgeIndex = null;
  renderEdges();
  document.querySelectorAll('[data-tab]').forEach(item => item.classList.toggle('active', item.dataset.tab === 'node'));
  document.querySelectorAll('.tab-panel').forEach(item => item.classList.toggle('active', item.id === 'nodePanel'));
  state.selectedNodeIds = id ? new Set([id]) : new Set();
  if (state.selectedNodeId === id) { syncNodeSelectionVisuals(); loadNodeSettings(); return; }
  state.selectedNodeId = id;
  $('#nodePanel').scrollTop = 0;
  syncNodeSelectionVisuals();
  loadNodeSettings();
}
function syncNodeSelectionVisuals() {
  document.querySelectorAll('.node').forEach(node => node.classList.toggle('selected', !state.selectedGroupId && (state.selectedNodeIds?.has(node.dataset.node) || node.dataset.node === state.selectedNodeId)));
  document.querySelectorAll('.links .edge').forEach(edge=>{
    edge.classList.toggle('edge-related',[edge.dataset.edgeFrom,edge.dataset.edgeTo].some(id=>state.selectedNodeIds?.has(id)||state.selectedNodeId===id));
  });
}
function appendNode(type, extra = {}) {
  if (!state.project) { toast('请先创建或打开一个项目'); return null; }
  if (state.nodes.length >= 200) { toast('画布最多 200 个节点，请先整理画布'); return null; }
  const canvas = $('#canvas');
  const node = { id: crypto.randomUUID(), type, title: {video:'视频生成',prompt:'文本提示词',asset:'图片素材',prop:'道具素材',character:'角色参考'}[type], x: (canvas.clientWidth / 2 - state.panX) / state.zoom - 140 + state.nodes.length % 4 * 24, y: (canvas.clientHeight / 2 - state.panY) / state.zoom - 90 + state.nodes.length % 4 * 24, settings: defaultSettings(), ...extra, ...(state.pendingNodePosition || {}) };
  state.pendingNodePosition = null;
  state.nodes.push(node); updateSelection(node.id); renderNodes(); scheduleSave(); closeCanvasPanel();
  return node;
}
function addVideoNode() { appendNode('video', { title: `视频生成 ${state.nodes.filter(n => n.type === 'video').length + 1}` }); toast('已添加独立视频节点'); }
function addScriptNode() {
  const source = selectedNode()?.type === 'prompt' ? selectedNode() : [...state.nodes].reverse().find(node => node.type === 'prompt');
  const node = appendNode('script', { title: '脚本生成器', sourceNodeId: source?.id || '', script_data: emptyScriptData(source?.id || '') });
  if (!node) return;
  if (source && source.id !== node.id && !state.edges.some(edge => edge.from === source.id && edge.to === node.id)) state.edges.push({ from: source.id, to: node.id });
  renderEdges(); scheduleSave(); toast(source ? '已添加脚本生成器，并连接文本节点' : '已添加脚本生成器，请连接文本节点');
}
function addCharacterNode(character) { const asset = state.assets.find(a => a.id === character.asset_id); appendNode('character', { title: `${character.name}参考`, characterId: character.id, characterName: character.name, description: character.description || character.role, tone: character.tone, assetId: asset?.id || '', assetUrl: asset?.url || '' }); }
function addPromptNode() { return appendNode('prompt', { title: '文本素材', settings: { ...defaultSettings(), prompt: '', negative_prompt: '' } }); }
function addAssetNode(asset, targetVideo = selectedNode()?.type === 'video' ? selectedNode() : null) {
  if (targetVideo && ['image', 'prop'].includes(asset.kind || 'image')) {
    selectAsset(asset.id, targetVideo);
    return state.nodes.find(node => ['asset', 'prop'].includes(node.type) && node.assetId === asset.id);
  }
  return addLibraryAsset(asset);
}
function isMediaNode(node) { return ['asset', 'prop', 'video-asset', 'audio'].includes(node?.type); }
function nodeMediaKind(node) { return node.type === 'video-asset' ? 'video' : node.type === 'audio' ? 'audio' : node.type === 'prop' ? 'prop' : 'image'; }
function nodeAsset(node) {
  const id = node.assetId || (node.type === 'character' ? state.characters.find(item => item.id === node.characterId)?.asset_id : '');
  return state.assets.find(item => item.id === id) || null;
}
function upsertAsset(asset) { const index = state.assets.findIndex(item => item.id === asset.id); if (index < 0) state.assets.unshift(asset); else state.assets[index] = asset; }
function previewGeneratedAsset(asset) {
  if (!asset) return;
  if (asset.id && !state.assets.some(item => item.id === asset.id)) state.assets.unshift(asset);
  assetLibrary.preview(asset);
}
function addLibraryAsset(asset, options = {}) {
  if (!state.project) return toast('请先创建或打开一个项目');
  upsertAsset(asset);
  showView('canvas');
  if (options.position) state.pendingNodePosition = options.position;
  const kind = asset.kind || 'image';
  let node;
  if (kind === 'text') node = appendNode('prompt', { title: asset.name, sourceAssetId: asset.id, settings: { ...defaultSettings(), prompt: asset.content || '', negative_prompt: '' } });
  else if (kind === 'script') node = appendNode('script', { title: asset.name, sourceAssetId: asset.id, script_data: { ...emptyScriptData(), ...structuredClone(asset.script_data || {}), source_node_id: '' } });
  else node = appendNode(({image:'asset',prop:'prop',video:'video-asset',audio:'audio'})[kind], { title: asset.name, assetId: asset.id, assetName: asset.name, assetUrl: asset.url });
  if (node?.type === 'script' && syncScriptResourcesToCanvas()) { renderNodes(); scheduleSave(); }
  if (node) toast(`${asset.name} 已添加到画布`);
  return node;
}
function bindNodeAsset(node, asset) {
  if (!state.nodes.includes(node)) return toast('原素材节点已移除，上传内容已保留在生成历史');
  const sameKind = nodeMediaKind(node) === (asset.kind || 'image') || (nodeMediaKind(node) === 'image' && (asset.kind || 'image') === 'prop');
  if (!sameKind) return toast('素材类型与节点不匹配，请选择同类文件');
  upsertAsset(asset);
  // Replacing a connected media node also replaces its bound script resources.
  for (const edge of state.edges.filter(item => item.from === node.id && item.relation !== 'agent-flow')) {
    const script = state.nodes.find(item => item.id === edge.to && item.type === 'script');
    for (const resource of script?.script_data?.resources || []) {
      if (node.assetId && (resource.asset_id || resource.assetId) === node.assetId && acceptsResourceAsset(resource, asset)) {
        Object.assign(resource, { asset_id: asset.id, file_name: asset.name, status: 'uploaded' });
        delete resource.assetId;
      }
    }
  }
  delete node.mediaWidth; delete node.mediaHeight;
  node.assetId = asset.id; node.assetUrl = asset.url; node.assetName = asset.name; node.title = node.type==='character'?`${node.characterName||'角色'}参考`:asset.name;
  if(node.scriptResourceTarget) {
    const script=state.nodes.find(item=>item.id===node.scriptResourceTarget.nodeId&&item.type==='script');
    const resource=script?.script_data?.resources?.find(item=>item.id===node.scriptResourceTarget.resourceId);
    if(resource&&acceptsResourceAsset(resource,asset))Object.assign(resource,{asset_id:asset.id,file_name:asset.name,status:'uploaded'});
  }
  syncScriptResourcesToCanvas();
  renderNodes();
  const current=selectedNode();
  if(current?.id===node.id || (['video','script'].includes(current?.type)&&state.edges.some(edge=>edge.from===node.id&&edge.to===current.id))) loadNodeSettings();
  scheduleSave();
  closeCanvasPanel();
}
function chooseNodeMedia(node) {
  inlineNodeMedia.pick(node);
}
function uploadNodeMedia(node) {
  const input = document.createElement('input'); input.type = 'file';
  input.accept = ({image:'.jpg,.jpeg,.png,.webp',prop:'.jpg,.jpeg,.png,.webp',video:'.mp4,.webm',audio:'.mp3,.wav,.m4a,.ogg'})[nodeMediaKind(node)];
  const projectId = state.project?.id;
  input.onchange = async () => {
    const file = input.files[0]; if (!file) return;
    try {
      const form = new FormData(); form.append('file', file); if (projectId) form.append('project_id', projectId); if (nodeMediaKind(node) === 'prop') form.append('kind', 'prop');
      toast('正在上传素材…');
      const asset = await api('/api/assets', { method: 'POST', body: form }); upsertAsset(asset);
      if (state.project?.id === projectId && state.nodes.includes(node)) { bindNodeAsset(node, asset); toast('素材已上传并绑定到节点'); }
      else toast('素材已加入生成历史');
    } catch (error) { toast(error.message); }
  };
  input.click();
}
function generateNodeImage(node) {
  if (['asset','prop','character'].includes(node?.type)) inlineNodeMedia.focus(node);
}
function generateNodeAudio(node, mode = '') {
  if (node?.type === 'audio') inlineNodeMedia.focus(node, mode);
}
async function prepareVideoReference(source) {
  if(source.type!=='video') {if(!nodeAsset(source)?.url)throw new Error('请先上传或选择参考视频');return nodeAsset(source);}
  const task=state.nodeTasks[source.id], projectId=state.project?.id;
  if(task?.status!=='done'||!task.video_url)throw new Error('请等待上游视频生成完成后再引用');
  const asset=await api(`/api/tasks/${encodeURIComponent(task.id)}/history-asset`,json({}));
  if(state.project?.id!==projectId||state.nodeTasks[source.id]?.id!==task.id)throw new Error('项目或上游视频已变更，请重新引用');
  const previousId=source.referenceAssetId;
  upsertAsset(asset);source.referenceAssetId=asset.id;source.referenceTaskId=task.id;
  for(const owner of state.nodes)for(const mention of owner.settings?.mentions||[]) {
    if(mention.node_id===source.id&&mention.kind==='video'&&mention.asset_id===previousId)mention.asset_id=asset.id;
  }
  return asset;
}
function referenceVideoPosition(reference) {
  const gap = 64;
  // Reserve the largest media-frame height so later portrait results still fit.
  const size = { ...nodeFootprint({ type: 'video' }), height: 334 };
  const occupied = state.nodes.map(node => {
    const box = nodeFootprint(node);
    return { x: node.x, y: node.y, width: box.width, height: node.type === 'video' ? Math.max(334, box.height) : box.height };
  });
  const x = reference.x + Math.max(350, nodeFootprint(reference).width + gap);
  let y = reference.y;
  // Keep the source-side column and move down past any occupied rectangles.
  while (true) {
    const collisions = occupied.filter(box => x < box.x + box.width + gap && x + size.width + gap > box.x && y < box.y + box.height + gap && y + size.height + gap > box.y);
    if (!collisions.length) return { x, y };
    y = Math.max(...collisions.map(box => box.y + box.height + gap));
  }
}
function generateFromImage(asset, source) {
  if (!asset || !['image', 'prop', 'video'].includes(asset.kind || 'image')) return;
  const reference = source && state.nodes.includes(source) ? source : addLibraryAsset(asset);
  if (!reference || state.edges.length >= 400) return;
  const position = referenceVideoPosition(reference);
  state.pendingNodePosition = null;
  const video = appendNode('video', { title: asset.kind==='video'?'参考视频生成':'参考图视频生成', ...position, settings: { ...defaultSettings(), prompt: '', asset_ids: [] } });
  if (!video) return;
  // Creating a reference changes the graph, not the user's current viewport.
  state.edges.push({ from: reference.id, to: video.id }); renderEdges(); loadNodeSettings(); scheduleSave();
  toast('已创建视频生成节点并连接参考素材，请填写文本提示或连接文本节点');
}
function expandScriptShots(node) {
  const completion = scriptExpansionStatus(node);
  if (!completion.complete) return toast(scriptExpansionHint(completion));
  const data = scriptDataForNode(node), scenes = data.scenes || [];
  if (!scenes.length) return toast('请先生成或导入分镜内容');
  const pending = scenes.filter(scene => !state.nodes.some(item => item.type === 'video' && item.scriptSourceId === node.id && Number(item.settings?.script_scene_index) === Number(scene.index)));
  if (!pending.length) return toast('这些分镜已有视频节点，可直接在画布编辑');
  if (state.nodes.length + pending.length > 200 || state.edges.length + pending.length > 400) return toast('展开后将超过画布容量，请减少镜头或新建画布');
  state.pendingNodePosition = null;
  for (const scene of pending) {
    const index = scenes.indexOf(scene);
    const storyboard = scriptVideoSettings(data, scene);
    const video = { id: crypto.randomUUID(), type: 'video', title: `第 ${index + 1} 镜 · 视频生成`, scriptSourceId: node.id, x: node.x + 360 + (index % 3) * 330, y: node.y + Math.floor(index / 3) * 250, settings: { ...defaultSettings(), prompt: storyboard.prompt, duration: storyboard.duration, negative_prompt: data.negative_prompt || '', script_scene_index: scene.index, prompt_source_id: node.id, script_source_id: node.id, asset_ids: [] } };
    state.nodes.push(video); state.edges.push({ from: node.id, to: video.id });
  }
  renderNodes(); loadNodeSettings(); scheduleSave(); toast(`已展开 ${pending.length} 个分镜节点，提示词随脚本同步，可逐镜头提交生成`);
}
function deleteNode(id) {
  deleteNodes([id]);
}
function deleteNodes(ids = []) {
  const removeIds = [...new Set(ids)].filter(id => state.nodes.some(node => node.id === id));
  if (!removeIds.length) return;
  const removedEdges = state.edges.filter(edge => removeIds.includes(edge.from) || removeIds.includes(edge.to));
  const remainingEdges = state.edges.filter(edge => !removeIds.includes(edge.from) && !removeIds.includes(edge.to));
  detachScriptResourceEdges(state.nodes, removedEdges, remainingEdges);
  state.nodes = state.nodes.filter(item => !removeIds.includes(item.id));
  state.groups = normalizeGroups(state.groups, state.nodes);
  state.selectedGroupId = null;
  state.edges = remainingEdges;
  state.pendingPort = null; state.selectedEdgeIndex = null;
  state.selectedNodeIds = new Set();
  const next = state.nodes[0]?.id || null;
  state.selectedNodeId = next;
  state.selectedNodeIds = next ? new Set([next]) : new Set();
  renderNodes(); loadNodeSettings(); scheduleSave();
  toast(removeIds.length > 1 ? `已删除 ${removeIds.length} 个节点，可按 Ctrl+Z 撤销` : '节点已删除，可按 Ctrl+Z 撤销');
}
function updatePortHighlights() {
  document.querySelectorAll('[data-node-port]').forEach(port => {
    const id = port.dataset.nodePort, output = port.dataset.port === 'out';
    port.classList.toggle('port-active', state.pendingPort === id && output);
    port.classList.toggle('port-target', Boolean(state.pendingPort && state.pendingPort !== id && !output));
    port.classList.toggle('port-connected', state.edges.some(edge => output ? edge.from === id : edge.to === id));
  });
  document.querySelectorAll('.node').forEach(node => node.classList.toggle('edge-source', state.pendingPort === node.dataset.node));
  if (!state.pendingPort) state.wireCursor = null;
  canvasConnections?.sync();
  renderEdges();
}
function connectionProblem(source, target) {
  if (!source || !target || source.id === target.id) return '请选择另一个节点';
  if (['asset', 'prop'].includes(target.type) && source.type !== 'prompt') {
    if (!['asset', 'prop'].includes(source.type)) return '图片素材只能连接文本，或其他图片、道具作为参考图';
    const reference = nodeAsset(source);
    if (!reference?.url) return '请先为图片节点上传或选择素材，再作为参考图连接';
    if ((reference.size_bytes || 0) > 10 * 1024 * 1024) return '参考图不能超过 10 MB，请压缩后再连接';
    const linked = state.edges.filter(edge => edge.to === target.id && edge.relation !== 'agent-flow').map(edge => state.nodes.find(item => item.id === edge.from)).filter(item => ['asset', 'prop'].includes(item?.type));
    if (linked.length >= 10) return '一张图片最多连接 10 张参考图';
  }
  if (target.type === 'video' && source.type === 'audio' && !nodeAsset(source)) return '请先为音频节点上传或生成音频素材';
  if (target.type === 'audio') {
    if (!['prompt', 'audio', 'asset', 'prop'].includes(source.type)) return '音频节点只能连接文本、音频或图片参考';
    if (source.type !== 'prompt') {
      if (!nodeAsset(source)) return '请先为参考节点绑定素材';
      const refs = state.edges.filter(item => item.to === target.id && item.relation !== 'agent-flow').map(item => state.nodes.find(candidate => candidate.id === item.from)).filter(item => item && ['audio', 'asset', 'prop'].includes(item.type));
      if (refs.length >= 3) return '音频节点最多连接 3 条参考音频';
      if (refs.length && (source.type !== 'audio' || refs.some(item => item.type !== 'audio'))) return '参考图片不能与其他图片或音频混用';
    }
  }
  if (target.type === 'script' && source.type !== 'prompt' && !isMediaNode(source)) return '脚本节点支持连接文本、图片、视频和音频素材';
  if (target.type === 'script' && isMediaNode(source) && !nodeAsset(source)) return '请先为素材节点上传或选择文件，再连接脚本';
  if (state.edges.length >= 400 && !state.edges.some(edge => edge.from === source.id && edge.to === target.id && edge.relation === 'agent-flow')) return '画布连线已达上限';
  return '';
}
async function connectPort(nodeId, port) {
  if (port === 'out') {
    state.pendingPort = state.pendingPort === nodeId ? null : nodeId;
    updatePortHighlights();
    toast(state.pendingPort ? '请选择目标节点，也可以按住 + 拖动连线' : '已取消连接');
    return;
  }
  if (!state.pendingPort) return toast('请先点击一个输出端口');
  if (state.pendingPort === nodeId) return toast('不能连接到自身，请选择另一个节点');
  const source = state.nodes.find(node => node.id === state.pendingPort), target = state.nodes.find(node => node.id === nodeId);
  const problem = connectionProblem(source,target);
  if (problem) return toast(problem);
  if (target?.type === 'video' && ['video-asset', 'video'].includes(source?.type)) {
    try { await prepareVideoReference(source); } catch(error) { return toast(error.message); }
    if(!state.nodes.includes(source)||!state.nodes.includes(target)||state.pendingPort!==source.id)return;
  }
  const flowIndex = state.edges.findIndex(edge => edge.from === state.pendingPort && edge.to === nodeId && edge.relation === 'agent-flow');
  const exists = state.edges.some(edge => edge.from === state.pendingPort && edge.to === nodeId && edge.relation !== 'agent-flow');
  if (exists) { state.pendingPort = null; updatePortHighlights(); return toast('这两个节点已经连接'); }
  const edge = { from: source.id, to: nodeId };
  if (target?.type === 'script' && isMediaNode(source)) {
    const asset = nodeAsset(source), data = scriptDataForNode(target);
    let resources = data.resources.filter(item => (item.asset_id || item.assetId) === asset.id && acceptsResourceAsset(item, asset));
    if (!resources.length) {
      const resource = { id: `resource-${crypto.randomUUID()}`, name: asset.name, kind: asset.kind || 'image', category: '画布参考', reason: '从画布连接的参考素材', required: false, asset_id: asset.id, file_name: asset.name, status: 'uploaded', scene_indices: [] };
      data.resources.push(resource); resources = [resource];
    }
    Object.assign(edge, { scriptResourceManaged: true, scriptResourceIds: resources.map(item => item.id) });
  }
  if (flowIndex >= 0) state.edges.splice(flowIndex, 1, edge);
  else state.edges.push(edge);
  state.pendingPort = null;
  state.selectedEdgeIndex = null;
  updatePortHighlights();
  renderEdges(); loadNodeSettings(); scheduleSave(); toast('节点已连接');
}

function startNodeDrag(event, node) {
  if (event.button !== 0 || state.drag || state.connectMode || state.canvasTool !== 'move' || state.spaceDown) return;
  event.preventDefault();
  event.stopPropagation();
  const nodeId = node.dataset.node;
  if(event.shiftKey)return;
  if(state.selectedGroupId)updateSelection(nodeId);
  const ids = state.selectedNodeIds?.has(nodeId) ? [...state.selectedNodeIds] : [nodeId];
  if (!state.selectedNodeIds?.has(nodeId)) updateSelection(nodeId);
  const items = ids.map(id => {
    const item = state.nodes.find(candidate => candidate.id === id);
    const element = document.querySelector(`.node[data-node="${CSS.escape(id)}"]`);
    return item && element ? { item, element, x: parseFloat(element.style.left), y: parseFloat(element.style.top) } : null;
  }).filter(Boolean);
  state.drag = { kind: 'node', node, items, pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, moved: false };
  // Capture only after actual movement; capturing here redirects title
  // clicks/double-clicks to the canvas and incorrectly opens the node palette.
}
function bindNodeEvents() {
  document.querySelectorAll('.node').forEach(node => {
    node.onclick = event => {
      if (state.canvasTool === 'hand' || state.spaceDown || event.target.closest('button,.port,video,audio,a,input,textarea,select')) return;
      if(event.shiftKey) {
        state.selectedGroupId=null;
        const id=node.dataset.node;
        if(state.selectedNodeIds.has(id))state.selectedNodeIds.delete(id);else state.selectedNodeIds.add(id);
        state.selectedNodeId=[...state.selectedNodeIds][0]||null;
        syncNodeSelectionVisuals();loadNodeSettings();
      } else updateSelection(node.dataset.node);
    };
    // Delegate from the card so replacing the <b> element after a rename does
    // not remove the next double-click interaction.
    node.ondblclick = event => {
      if (!event.target.closest('.node-title b')) return;
      beginNodeTitleEdit(state.nodes.find(item => item.id === node.dataset.node), event);
    };
    node.onpointerdown = event => {
      if (!event.target.closest('button,.port,input,textarea,select,video,audio,a,.media-node-interactive')) startNodeDrag(event, node);
    };
  });
  document.querySelectorAll('[data-delete-node]').forEach(button => button.onclick = event => {
    event.stopPropagation();
    deleteNode(button.dataset.deleteNode);
  });
  document.querySelectorAll('[data-open-script]').forEach(button => button.onclick = event => { event.stopPropagation(); openScriptWorkflow(button.dataset.openScript); });
  document.querySelectorAll('[data-edit-text]').forEach(button => button.onclick = event => {
    event.stopPropagation();
    openTextNode(button.dataset.editText);
  });
  document.querySelectorAll('.node').forEach(node => {
    node.addEventListener('keydown', event => {
      if (event.target === node && (event.key === 'Enter' || event.key === ' ')) { event.preventDefault(); updateSelection(node.dataset.node); }
    });
  });
  document.querySelectorAll('[data-node-port]').forEach(port => {
    port.setAttribute('role', 'button'); port.tabIndex = 0;
    port.onkeydown = event => { if (event.key === 'Enter' || event.key === ' ') { event.preventDefault(); event.stopPropagation(); port.click(); } };
    port.onclick = event => {
    event.stopPropagation();
    if (state.canvasTool !== 'hand' && !state.spaceDown) { updateSelection(port.dataset.nodePort); connectPort(port.dataset.nodePort, port.dataset.port); }
    };
  });
  document.querySelectorAll('[data-media-pick]').forEach(button => button.onclick = () => { const node = state.nodes.find(item => item.id === button.dataset.mediaPick); if (node) chooseNodeMedia(node); });
  document.querySelectorAll('[data-media-upload]').forEach(button => button.onclick = () => { const node = state.nodes.find(item => item.id === button.dataset.mediaUpload); if (node) uploadNodeMedia(node); });
  document.querySelectorAll('[data-media-generate]').forEach(button => button.onclick = () => { const node = state.nodes.find(item => item.id === button.dataset.mediaGenerate); if (node) generateNodeImage(node); });
  document.querySelectorAll('[data-audio-generate]').forEach(button => button.onclick = () => { const node = state.nodes.find(item => item.id === button.dataset.audioGenerate); if (node) generateNodeAudio(node, button.dataset.audioMode); });
  updatePortHighlights();
}
function nodeFootprint(node) {
  return isVisualFrame(node) ? { width: frameBox(node).width, height: frameBox(node).height + 34 } : { width: 280, height: 340 };
}
function canvasMinZoom() { return .05; }
function fitCanvas(nodes = state.nodes) {
  const canvas = $('#canvas'); if (!canvas || !nodes.length) return;
  const rect = canvas.getBoundingClientRect();
  const boxes=nodes.map(node=>({x:node.x,y:node.y,...nodeFootprint(node)}));
  for(const group of state.groups)if(group.node_ids.some(id=>nodes.some(node=>node.id===id))){const box=groupBounds(group,state.nodes,nodeFootprint);if(box)boxes.push({...box,y:box.y-24,height:box.height+24});}
  const minX = Math.min(...boxes.map(box => box.x)) - 26;
  const minY = Math.min(...boxes.map(box => box.y));
  const maxX = Math.max(...boxes.map(box => box.x + box.width)) + 26;
  const maxY = Math.max(...boxes.map(box => box.y + box.height)) + 30;
  const width = Math.max(280, maxX - minX), height = Math.max(180, maxY - minY);
  state.zoom = Math.max(canvasMinZoom(), Math.min(1.15, (rect.width-70)/width, (rect.height-100)/height));
  state.panX = (rect.width-width*state.zoom)/2-minX*state.zoom;
  state.panY = (rect.height-height*state.zoom)/2-minY*state.zoom;
  setZoom(state.zoom);
}
function zoomAt(clientX, clientY, factor) { const canvas = $('#canvas'); const rect = canvas.getBoundingClientRect(); const localX = clientX - rect.left; const localY = clientY - rect.top; const worldX = (localX - state.panX) / state.zoom; const worldY = (localY - state.panY) / state.zoom; const nextZoom = Math.max(canvasMinZoom(), Math.min(8, state.zoom * factor)); state.zoom = nextZoom; state.panX = localX - worldX * nextZoom; state.panY = localY - worldY * nextZoom; setZoom(nextZoom); scheduleSave(); }
function finishCanvasDrag(event) {
  const canvas = $('#canvas');
  const drag = state.drag;
  if (!drag || (event?.pointerId != null && event.pointerId !== drag.pointerId)) return;
  state.drag = null;
  if (drag.kind === 'node' && drag.moved && state.snapToGrid) {
    for (const { item, element } of drag.items || []) {
      item.x = Math.round(item.x / 24) * 24;
      item.y = Math.round(item.y / 24) * 24;
      element.style.left = item.x + 'px'; element.style.top = item.y + 'px';
    }
    renderEdges();
  }
  canvasWorkspace?.refresh();
  canvas.classList.remove('is-moving', 'is-panning');
  if (canvas.hasPointerCapture(drag.pointerId)) canvas.releasePointerCapture(drag.pointerId);
  if (drag.kind === 'select') {
    hideCanvasSelection();
    if (drag.moved) {
      const ids = [...(drag.ids || [])];
      state.selectedNodeIds = new Set(ids);
      state.selectedNodeId = ids[0] || null;
      state.selectedEdgeIndex = null;
      syncNodeSelectionVisuals();
      loadNodeSettings();
      state.suppressClickUntil = performance.now() + 100;
    } else {
      updateSelection(null);
    }
    return;
  }
  if (drag.moved) {
    state.suppressClickUntil = performance.now() + 100;
    scheduleSave();
  }
}
function canvasSelectionRect(startX, startY, endX, endY) {
  const canvas = $('#canvas');
  const rect = canvas.getBoundingClientRect();
  const left = Math.max(0, Math.min(startX, endX) - rect.left);
  const top = Math.max(0, Math.min(startY, endY) - rect.top);
  const right = Math.min(rect.width, Math.max(startX, endX) - rect.left);
  const bottom = Math.min(rect.height, Math.max(startY, endY) - rect.top);
  return { left, top, right, bottom, width: Math.max(0, right - left), height: Math.max(0, bottom - top) };
}
function updateCanvasSelection(drag, event) {
  const box = canvasSelectionRect(drag.startX, drag.startY, event.clientX, event.clientY);
  const selection = $('#canvasSelection');
  if (selection) {
    selection.hidden = false;
    selection.style.left = `${box.left}px`;
    selection.style.top = `${box.top}px`;
    selection.style.width = `${box.width}px`;
    selection.style.height = `${box.height}px`;
  }
  const ids = [];
  document.querySelectorAll('#canvasStage .node').forEach(element => {
    const rect = element.getBoundingClientRect();
    const intersects = rect.right >= drag.startX && rect.left <= event.clientX || rect.right >= event.clientX && rect.left <= drag.startX;
    const vertical = rect.bottom >= drag.startY && rect.top <= event.clientY || rect.bottom >= event.clientY && rect.top <= drag.startY;
    if (intersects && vertical) ids.push(element.dataset.node);
  });
  drag.ids = ids;
  state.selectedNodeIds = new Set(ids);
  state.selectedNodeId = ids[0] || null;
  state.selectedEdgeIndex = null;
  syncNodeSelectionVisuals();
}
function hideCanvasSelection() {
  const selection = $('#canvasSelection');
  if (selection) selection.hidden = true;
}
function bindCanvas() {
  const canvas = $('#canvas');
  canvasConnections = installCanvasConnections({ canvas, state, connect:connectPort, renderEdges,
    highlights:updatePortHighlights, onError:toast,
    canConnect:id=>!connectionProblem(state.nodes.find(node=>node.id===state.pendingPort),state.nodes.find(node=>node.id===id)) });
  // Canvas dragging prevents the browser's default focus change. Commit the
  // name before an outside gesture so clicking blank canvas also ends editing.
  document.addEventListener('pointerdown', event => {
    const input=state.titleEdit?.input;
    if(input&&event.target!==input)input.blur();
  }, true);
  const chrome = '.canvas-dock,.canvas-panel,.inspector,.workspace-chrome';
  let selection = $('#canvasSelection');
  if (!selection) {
    selection = document.createElement('div');
    selection.id = 'canvasSelection';
    selection.className = 'canvas-selection';
    selection.hidden = true;
    canvas.appendChild(selection);
  }
  // A new gesture is intentional, even immediately after a completed drag.
  canvas.addEventListener('pointerdown', () => { state.suppressClickUntil = 0; }, true);
  canvas.onpointerdown = event => {
    if (state.drag || ![0, 1].includes(event.button) || event.target.closest(chrome)) return;
    const pan = event.button === 1 || state.canvasTool === 'hand' || state.spaceDown;
    if (!pan) {
      if (state.connectMode) return;
      if (!event.target.closest('.node,[data-edge]')) {
        state.pendingPort = null; state.wireCursor = null; updatePortHighlights(); closeToolMenu();
        event.preventDefault();
        state.drag = { kind: 'select', pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, moved: false, ids: [] };
        state.selectedNodeIds = new Set(); state.selectedNodeId = null; state.selectedEdgeIndex = null; state.selectedGroupId = null;
        syncNodeSelectionVisuals();
        canvas.setPointerCapture(event.pointerId);
        hideCanvasSelection();
      }
      return;
    }
    event.preventDefault();
    state.drag = { kind: 'pan', pointerId: event.pointerId, startX: event.clientX, startY: event.clientY, x: state.panX, y: state.panY, moved: false };
    canvas.classList.add('is-panning');
    canvas.setPointerCapture(event.pointerId);
  };
  canvas.onpointermove = event => {
    const rect = canvas.getBoundingClientRect();
    if (state.pendingPort) {
      state.wireCursor = { x: (event.clientX - rect.left - state.panX) / state.zoom, y: (event.clientY - rect.top - state.panY) / state.zoom };
      renderEdges();
      canvasWorkspace?.refresh();
    }
    const drag = state.drag;
    if (!drag || event.pointerId !== drag.pointerId) return;
    const dx = event.clientX - drag.startX, dy = event.clientY - drag.startY;
    if (!drag.moved && Math.hypot(dx, dy) < 3) return;
    if (!drag.moved && drag.kind === 'node') {
      canvas.setPointerCapture(event.pointerId);
      canvas.classList.add('is-moving');
    }
    drag.moved = true;
    if (drag.kind === 'select') {
      updateCanvasSelection(drag, event);
    } else if (drag.kind === 'node') {
      const items = drag.items || [];
      if (!items.length) return finishCanvasDrag(event);
      items.forEach(({ item, element, x, y }) => {
        item.x = x + dx / state.zoom;
        item.y = y + dy / state.zoom;
        element.style.left = item.x + 'px';
        element.style.top = item.y + 'px';
      });
      renderEdges();
      canvasWorkspace?.refresh();
    } else {
      state.panX = drag.x + dx;
      state.panY = drag.y + dy;
      setZoom(state.zoom);
    }
  };
  canvas.ondblclick = event => {
    if (event.target.closest('.node,.canvas-group,[data-edge],' + chrome) || state.canvasTool === 'hand' || state.spaceDown) return;
    const rect = canvas.getBoundingClientRect();
    showNodePalette({ x: (event.clientX - rect.left - state.panX) / state.zoom, y: (event.clientY - rect.top - state.panY) / state.zoom });
  };
  canvas.onpointerup = finishCanvasDrag;
  canvas.onpointercancel = finishCanvasDrag;
  canvas.onlostpointercapture = finishCanvasDrag;
  canvas.addEventListener('click', event => {
    if (event.target.closest(chrome)) return;
    if (performance.now() < (state.suppressClickUntil || 0) || state.canvasTool === 'hand' || state.spaceDown) {
      event.preventDefault();
      event.stopImmediatePropagation();
    }
  }, true);
  canvas.addEventListener('wheel', event => {
    if (event.target.closest(chrome + ',video,audio')) return;
    event.preventDefault();
    if (!state.drag && event.deltaY) zoomAt(event.clientX, event.clientY, event.deltaY < 0 ? 1.1 : .9);
  }, { passive: false });
  window.addEventListener('keydown', event => {
    if (event.isComposing || event.ctrlKey || event.metaKey || event.altKey || !$('#canvasView').classList.contains('active-view') || !$('#modalBackdrop').hidden || Boolean(document.getElementById('imageGenerationOverlay'))) return;
    if (event.target.closest?.('input,textarea,select,[contenteditable]:not([contenteditable="false"])')) return;
      if (event.code === 'Escape') { closeToolMenu(true); closeCanvasPanel(); state.pendingPort = null; state.selectedEdgeIndex = null; updatePortHighlights(); renderEdges(); return; }
    if (event.code === 'Space' && canvas.matches(':hover') && !event.target.closest('button,video,audio')) {
      event.preventDefault();
      state.spaceDown = true;
      canvas.classList.add('space-pan');
    }
    if (!event.repeat && !state.drag && (event.code === 'KeyV' || event.code === 'KeyH')) {
      event.preventDefault();
      setCanvasTool(event.code === 'KeyH' ? 'hand' : 'move', false);
    }
  });
  const releaseSpace = () => { state.spaceDown = false; canvas.classList.remove('space-pan'); };
  window.addEventListener('keyup', event => { if (event.code === 'Space') releaseSpace(); });
  window.addEventListener('blur', () => { finishCanvasDrag(); releaseSpace(); closeToolMenu(); });
  document.addEventListener('visibilitychange', () => { if (document.hidden) { finishCanvasDrag(); releaseSpace(); } });
  $('#zoomIn').onclick = () => zoomAt(canvas.getBoundingClientRect().left + canvas.clientWidth / 2, canvas.getBoundingClientRect().top + canvas.clientHeight / 2, 1.1);
  $('#zoomOut').onclick = () => zoomAt(canvas.getBoundingClientRect().left + canvas.clientWidth / 2, canvas.getBoundingClientRect().top + canvas.clientHeight / 2, .9);
  $('#fitBtn').onclick = () => { fitCanvas(); scheduleSave(); };
  $('#resetCanvas').onclick = () => { state.panX = 0; state.panY = 0; setZoom(1); scheduleSave(); };
}
function setZoom(value) { canvasGroups?.refresh(); state.zoom = Math.max(canvasMinZoom(), Math.min(8, value)); $('#canvasStage').style.transform = `translate(${state.panX}px,${state.panY}px) scale(${state.zoom})`; $('#zoomText').textContent = `${Math.round(state.zoom * 100)}%`; canvasWorkspace?.refresh(); }

function updateTaskNode(task, nodeId = state.selectedNodeId) {
  const targetId = nodeId || state.nodes.find(node => node.type === 'video')?.id;
  if (!targetId) return;
  if (task) state.nodeTasks[targetId] = task;
  task = state.nodeTasks[targetId];
  syncVideoGenerateButton();
  const node = document.querySelector(`.node[data-node="${CSS.escape(targetId)}"]`);
  if (!node) return;
  const modelNode = state.nodes.find(item => item.id === targetId);
  const meta = node.querySelector('.node-meta');
  const preview = node.querySelector('.video-preview');
  const dot = node.querySelector('.status-dot');
  if (dot) dot.className = `status-dot ${videoStatusClass(task, modelNode)}`;
  if (meta && modelNode) meta.outerHTML = videoMetaHtml(modelNode, task);
  if (preview) {
    preview.dataset.playbackFailed = 'false';
    preview.innerHTML = videoPreviewBody(modelNode, task);
    const frameMedia = preview.querySelector('.frame-media');
    if (frameMedia) watchFrameMedia(modelNode, node, frameMedia);
    else clearFrameSize(modelNode, node);
    bindVideoPlayback(preview.querySelector('video'), task);
  }
  if(task?.status==='done'&&modelNode?.referenceTaskId&&modelNode.referenceTaskId!==task.id) {
    prepareVideoReference(modelNode).then(()=>{renderAssets();scheduleSave();}).catch(error=>toast(error.message));
  }
  canvasWorkspace?.refresh();
}
async function pollTask(taskId, nodeId) {
  if (state.taskPollers.has(taskId)) return;
  const projectId = state.project?.id;
  const poll = async () => {
    if (state.project?.id !== projectId) { state.taskPollers.delete(taskId); return; }
    try {
      const task = await api(`/api/tasks/${taskId}`);
      if (state.project?.id !== projectId) { state.taskPollers.delete(taskId); return; }
      if (state.nodeTasks[nodeId]?.id === taskId) updateTaskNode(task, nodeId);
      if (task.status === 'done' && !(task.video_cache_status === 'pending') || task.status === 'failed' || task.status === 'blocked') {
        state.taskPollers.delete(taskId);
        if (task.status === 'done') state.lastCompletedTask = task;
        await loadHistory();
        toast(task.status === 'done' ? '视频生成完成' : describeTaskError(task).quotaLimited ? '模型用量达到上限，请在任务记录中查看处理方法' : describeTaskError(task).message);
        return;
      }
      state.taskPollers.set(taskId, window.setTimeout(poll, 2200));
    } catch (error) {
      if ([401,403,404,428].includes(error.status)) { state.taskPollers.delete(taskId); return; }
      state.taskPollers.set(taskId, window.setTimeout(poll, 5000));
    }
  };
  state.taskPollers.set(taskId, window.setTimeout(poll, 900));
}
async function submitTask() {
  const videoNode = selectedNode(), projectId = state.project?.id;
  if (videoNode?.type !== 'video') return toast('请先选择视频生成节点');
  if (state.switchingProject || videoGenerationBusy(videoNode.id, projectId)) return;
  const submissionKey = videoSubmissionKey(videoNode.id, projectId);
  let submitting = false;
  try {
    captureSettings();
    const mode = deriveVideoMode(videoNode, state.nodes, state.edges, state.assets, state.characters);
    state.mode = mode;
    const input = resolveVideoInput(videoNode, state.nodes, state.edges);
    const prompt = input.prompt.trim();
    if (!prompt) return toast(input.source?.type === 'script' ? '请先在脚本第三步合成该镜头的提示词' : input.source ? '上游文本节点内容为空，请先填写文本内容' : '请先填写提示词');
    if (prompt.length > 4000) return toast(input.source?.type === 'script' ? '该镜头的两段提示词合计超过 4000 字，请在脚本第三步精简后重试' : '提示词超过 4000 字，请精简后重试');
    const references = resolveVideoAssets(videoNode, state.nodes, state.edges, state.assets, state.characters);
    if (references.missing.length) return toast('参考节点尚未绑定素材或素材已失效，请检查参考素材');
    let selectedAssets = references.entries.map(entry => entry.asset);
    if(mode==='first-last'){
      const ids=videoNode.settings.frame_asset_ids||[];
      if(ids.length!==2||ids.some(id=>!id))return toast('请上传首帧和尾帧图片');
      selectedAssets=ids.map(id=>state.assets.find(asset=>asset.id===id));
      if(selectedAssets.some(asset=>!asset||!['image','prop'].includes(asset.kind)))return toast('首尾帧仅支持有效的图片素材');
    }
    const model = selectedModel();
    if (!model) return toast('请先选择模型');
    const storyboard = input.source?.type === 'script' && input.scene ? scriptVideoSettings(input.source.script_data || {}, input.scene) : null;
    const params = storyboard ? { ...videoNode.settings, prompt: storyboard.prompt, duration: storyboard.duration } : videoNode.settings;
    const caps = model.capabilities || {};
    if (storyboard) {
      const unsupported = [caps.aspect_ratios?.length && !caps.aspect_ratios.includes(params.aspect_ratio) ? `画幅 ${params.aspect_ratio}` : '', caps.durations?.length && !caps.durations.includes(Number(params.duration)) ? `时长 ${params.duration} 秒` : '', caps.resolutions?.length && !caps.resolutions.includes(params.resolution) ? `分辨率 ${String(params.resolution).toUpperCase()}` : ''].filter(Boolean);
      if (unsupported.length) return toast(`当前模型不支持${unsupported.join('、')}，请在视频节点调整画幅或分辨率，或在脚本中调整镜头时长`);
    }
    const payload = { project_id: state.project.id, node_id: videoNode.id, prompt, negative_prompt: input.negative_prompt.trim(), mode: state.mode, model: model.model_id, model_config_id: model.id, aspect_ratio: params.aspect_ratio, duration: Number(params.duration), resolution: params.resolution, visual_style: params.visual_style||null, asset_ids: selectedAssets.map(asset => asset.id) };
    videoSubmissions.add(submissionKey); submitting = true;
    syncVideoGenerateButton();
    await flushSave();
    const task = await api('/api/tasks', json(payload));
    if (state.project?.id !== projectId) return;
    updateTaskNode(task, videoNode.id);
    await loadHistory();
    if (state.project?.id !== projectId) return;
    if (task.status === 'queued' || task.status === 'running') pollTask(task.id, videoNode.id);
    else toast(task.error || (task.status === 'blocked' ? '请先配置模型 API Key' : '任务已提交'));
    if (task.status === 'queued' || task.status === 'running') toast('任务已提交，正在排队或生成');
  } catch (error) { toast(error.message); }
  finally {
    if (submitting) videoSubmissions.delete(submissionKey);
    syncVideoGenerateButton();
  }
}
async function loadHistory() {
  if (!state.project) return;
  const projectId = state.project.id;
  const tasks = await api(`/api/tasks?project_id=${projectId}`);
  if (state.project?.id !== projectId) return;
  const sortedTasks = [...tasks].sort((a, b) => (b.created_at || b.updated_at || 0) - (a.created_at || a.updated_at || 0));
  const latestByNode = {};
  sortedTasks.forEach(task => {
    const nodeId = task.params?.node_id, current = latestByNode[nodeId];
    if (nodeId && (!current || ['queued','running'].includes(task.status) && !['queued','running'].includes(current.status))) latestByNode[nodeId] = task;
  });
  state.nodeTasks = { ...state.nodeTasks, ...latestByNode };
  syncVideoGenerateButton();
  Object.entries(latestByNode).forEach(([nodeId, task]) => {
    updateTaskNode(task, nodeId);
    if (['queued','running'].includes(task.status) || task.video_cache_status === 'pending') pollTask(task.id, nodeId);
  });
  const latestDone = sortedTasks.find(task => task.status === 'done' && task.video_url);
  if (latestDone) state.lastCompletedTask = latestDone;
  $('#taskHistory').innerHTML = tasks.length ? tasks.map(task => `<div class="task-item"><b>${escapeHtml(task.params.prompt.slice(0, 32))}${task.params.prompt.length > 32 ? '…' : ''}</b><p>${escapeHtml(task.params.model_name || task.params.model)} · ${escapeHtml(task.params.resolution)} · ${task.params.duration}s</p><span class="task-status ${task.status}">${statusText(task.status, task)}</span>${taskErrorHtml(task)}${task.status === 'done' && task.video_url ? `<button class="history-download" data-download-task="${escapeHtml(task.id)}">下载视频</button>` : ''}${taskRetryHtml(task)}</div>`).join('') : '<div class="empty">还没有生成任务</div>';
  $('#taskHistory').querySelectorAll('[data-download-task]').forEach(button => button.onclick = () => downloadTask(tasks.find(task => task.id === button.dataset.downloadTask)));
  $('#taskHistory').querySelectorAll('[data-retry-task]').forEach(button => button.onclick = () => retryTask(tasks.find(task => task.id === button.dataset.retryTask)));

}
async function retryTask(task, accountReviewed = false) {
  if (!task?.params) return;
  const projectId = task.project_id || task.params.project_id, nodeId = task.params.node_id;
  if (state.project?.id !== projectId || state.switchingProject || videoGenerationBusy(nodeId, projectId)) return;
  if (describeTaskError(task).quotaLimited && !accountReviewed) return showQuotaHelp(task);
  const submissionKey = videoSubmissionKey(nodeId, projectId);
  videoSubmissions.add(submissionKey);
  syncVideoGenerateButton();
  try {
    const payload = { ...task.params }; delete payload.model_name;
    const next = await api('/api/tasks', json(payload));
    if (state.project?.id !== projectId) return;
    if (accountReviewed) closeModal();
    updateTaskNode(next, task.params.node_id); await loadHistory();
    if (state.project?.id !== projectId) return;
    if (['queued','running'].includes(next.status)) pollTask(next.id, task.params.node_id); else toast(next.error || '任务未执行');
  } catch (error) { toast(error.message); }
  finally { videoSubmissions.delete(submissionKey); syncVideoGenerateButton(); }
}
function renderAssetLibrary() { assetLibrary.refresh(); }
function showView(view, conversationCommit=false) {
  if(!conversationCommit)conversationOpenRevision++;
  const homeRequested=view==='home';if(homeRequested)view='agent';
  if (view === 'works') view = 'projects';
  if(view!=='agent')homeComposer?.hide();
  canvasAgent?.onView(view);
  if (state.view === 'canvas' && view !== 'canvas') {
    canvasConnections?.reset();
    document.querySelectorAll('#canvasView video,#canvasView audio,#inspector video,#inspector audio').forEach(media=>media.pause());
  }
  if (state.view === 'assets' && view !== 'assets') assetLibrary.stopPreviews();
  if (state.view === 'models' && view !== 'models' && state.modelReturn) { state.modelReturn = null; imageGenerator.close(); audioGenerator.close(); }
  $('#libraryGrid').classList.remove('asset-library-host','project-library-host');
  state.view = view;
  homeHub?.onView();
  renderProjects();
  document.body.classList.toggle('assets-active', view === 'assets');
  document.body.classList.toggle('projects-active', view === 'projects');
  document.body.classList.toggle('canvas-active', view === 'canvas');
  canvasWorkspace?.onView(view);
  document.body.classList.toggle('agent-active', view === 'agent');
  if (view !== 'agent'&&!canvasAgent?.isOpen()) agentChat.hide();
  document.querySelectorAll('#mainNav [data-view]').forEach(button => {
    const active = button.dataset.view === 'home' ? ['agent','canvas'].includes(view) : button.dataset.view === view;
    button.classList.toggle('active', active);
    if (active) button.setAttribute('aria-current', 'page'); else button.removeAttribute('aria-current');
  });
  document.querySelectorAll('.view').forEach(el => el.classList.remove('active-view'));
  const canvasView = view === 'canvas';
  $('#inspector').hidden = !canvasView;
  $('#renameProject').hidden = !canvasView;
  $('#projectBreadcrumb').hidden = ['models', 'agent'].includes(view);
  $('#modelPageBreadcrumb').hidden = view !== 'models';
  $('#agentBreadcrumb').hidden = view !== 'agent';
  if (view === 'agent') { $('#agentView').classList.add('active-view');if(homeRequested)homeHub?.goHome(new URLSearchParams(location.search).get('mode')==='canvas'?'canvas':undefined).catch(error=>toast(error.message));agentChat.show(); homeComposer?.show(); return; }
  if (view === 'models') {
    $('#modelsView').classList.add('active-view');
    $('#modelsBack').textContent = state.modelReturn?.workflow ? '← 返回脚本编辑' : state.modelReturn?.onReturn ? state.modelCategory === 'audio' ? '← 返回音频创作' : '← 返回图片创作' : '← 返回创作';
    modelManager.open(state.modelCategory || 'text');
    return;
  }
  if (canvasView) { $('#canvasView').classList.add('active-view'); if (!state.project && state.projects.length && !state.switchingProject) selectProject(state.projects[0].id); return; }
  $('#libraryView').classList.add('active-view');
  $('#libraryTitle').textContent = view === 'projects' ? '项目' : '资产';
  $('#libraryAction').hidden = true;
  if (view === 'projects') projectLibrary.open(); else if (view === 'assets') renderAssetLibrary();
}
function setRailState(side, collapsed, persist = true) {
  const isSidebar = side === 'sidebar';
  const key = isSidebar ? 'sidebarCollapsed' : 'inspectorCollapsed';
  const className = isSidebar ? 'sidebar-collapsed' : 'inspector-collapsed';
  const button = $(isSidebar ? '#sidebarToggle' : '#inspectorToggle');
  state[key] = Boolean(collapsed);
  document.body.classList.toggle(className, state[key]);
  if (button) {
    button.setAttribute('aria-expanded', String(!state[key]));
    button.setAttribute('aria-label', `${state[key] ? '展开' : '折叠'}${isSidebar ? '左侧导航' : '右侧详情栏'}`);
  }
  if (persist) localStorage.setItem(`jianhuitong.${key}`, state[key] ? '1' : '0');
  if (button) button.title = button.getAttribute('aria-label');
  $('#inspector .inspector-body').inert = state.inspectorCollapsed;
  document.querySelectorAll('.brand-copy,.sidebar .new-btn,.project-list,.side-bottom').forEach(content => { content.inert = state.sidebarCollapsed; });
}
function bindRailToggles() {
  const sidebarCollapsed = localStorage.getItem('jianhuitong.sidebarCollapsed') === '1';
  const inspectorCollapsed = localStorage.getItem('jianhuitong.inspectorCollapsed') === '1';
  $('#sidebarToggle').onclick = () => setRailState('sidebar', !state.sidebarCollapsed);
  $('#inspectorToggle').onclick = () => setRailState('inspector', !state.inspectorCollapsed);
  setRailState('sidebar', sidebarCollapsed, false);
  setRailState('inspector', inspectorCollapsed, false);
  requestAnimationFrame(() => requestAnimationFrame(() => document.body.classList.add('rails-ready')));
}
function animateNavigationIcon(button) {
  if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return;
  const glyph = button.querySelector('.nav-icon-motion');
  if (!glyph) return;
  glyph.getAnimations().forEach(animation => animation.cancel());
  const motion = {
    home: ['scale(1) rotate(0deg)', 'scale(1.2) rotate(65deg)', 'scale(.94) rotate(95deg)', 'scale(1) rotate(90deg)'],
    projects: ['translateY(0)', 'translateY(-3px) rotate(-5deg)', 'translateY(1px) rotate(2deg)', 'translateY(0)'],
    assets: ['scale(1)', 'scale(1.13) rotate(-7deg)', 'scale(.97) rotate(3deg)', 'scale(1)'],
    models: ['rotate(0deg)', 'rotate(-12deg)', 'rotate(8deg)', 'rotate(0deg)'],
  };
  glyph.animate((motion[button.dataset.view] || motion.home).map(transform => ({ transform })), {
    duration: 460, easing: 'cubic-bezier(.22,.75,.25,1)',
  });
}
function bindNavigation() { document.querySelectorAll('#mainNav button').forEach(button => button.onclick = () => { animateNavigationIcon(button); if (button.dataset.view === 'models') return openModelManagement('text'); document.querySelectorAll('#mainNav button').forEach(item => item.classList.remove('active')); button.classList.add('active'); showView(button.dataset.view); }); document.querySelectorAll('[data-tab]').forEach(button => button.onclick = () => { document.querySelectorAll('[data-tab]').forEach(item => item.classList.remove('active')); document.querySelectorAll('.tab-panel').forEach(item => item.classList.remove('active')); button.classList.add('active'); $(`#${button.dataset.tab}Panel`).classList.add('active'); if (button.dataset.tab === 'history') loadHistory(); }); }

function showModal(content, modifier = '') { $('#modalContent').querySelectorAll('video,audio').forEach(media=>media.pause()); $('#modal').className = `modal ${modifier}`.trim(); $('#modalContent').innerHTML = content; $('#modalBackdrop').hidden = false; const input = $('#modalContent input'); if (input) { input.focus(); input.select(); } }
function closeModal() { $('#modalContent').querySelectorAll('video,audio').forEach(media=>media.pause()); $('#modalBackdrop').hidden = true; $('#modalContent').innerHTML = ''; state.scriptWorkflow = null; state.pendingScriptResourceId = ''; }
function newProjectModal(folder = '') { state.pendingProjectFolderId = typeof folder === 'string' ? folder : '';  showModal(`<h2>新建画布</h2><p>从文本、图片或视频节点开始创作。</p><label for="newProjectInput">项目名称</label><input id="newProjectInput" value="未命名项目" maxlength="80"><div class="modal-actions"><button class="secondary" id="cancelModal">取消</button><button class="primary" id="confirmNewProject">创建画布</button></div>`); $('#cancelModal').onclick = closeModal; $('#confirmNewProject').onclick = createProject; $('#newProjectInput').onkeydown = event => { if (event.key === 'Enter') createProject(); }; }
async function createProject() {
  const name = $('#newProjectInput').value.trim(); if (!name) return toast('请输入项目名称');
  const button = $('#confirmNewProject'); button.disabled = true;
  try { await flushSave(); const project = await api('/api/projects', json({name, nodes: [], edges: [], folder_id: state.pendingProjectFolderId || ''})); state.projects.unshift(project); closeModal(); await selectProject(project.id); toast('画布已创建'); } catch(error) { toast(error.message); button.disabled = false; }
}
function editProjectNameInline() {
  if (!state.project) return;
  const existing = $('#projectNameInline');
  if (existing) { existing.focus(); return; }
  const title = $('#projectName'), projectId = state.project.id;
  const input = document.createElement('input');
  input.id = 'projectNameInline'; input.type = 'text'; input.maxLength = 80;
  input.setAttribute('aria-label', '画布名称'); input.autocomplete = 'off';
  input.value = $('#projectNameInput').value || state.project.name;
  const resize = () => { input.style.width = `${Math.min(240, Math.max(60, input.value.length * 14 + 8))}px`; };
  resize(); title.before(input); title.hidden = true;
  let finished = false;
  const finish = save => {
    if (finished) return;
    finished = true;
    document.removeEventListener('pointerdown', outside, true);
    const name = input.value.trim(); input.remove(); title.hidden = false;
    if (!save || state.project?.id !== projectId) return;
    if (!name) { toast('项目名称不能为空'); return; }
    if (name === $('#projectNameInput').value.trim()) return;
    title.textContent = name; $('#canvasTitle').textContent = name;
    $('#projectNameInput').value = name; scheduleSave();
    flushSave().catch(() => {});
  };
  const outside = event => { if (!input.contains(event.target) && !event.target.closest('#renameProject')) finish(true); };
  document.addEventListener('pointerdown', outside, true);
  input.oninput = resize;
  input.onblur = () => finish(true);
  input.onkeydown = event => {
    if (event.isComposing || !['Enter', 'Escape'].includes(event.key)) return;
    event.preventDefault(); event.stopPropagation();
    finish(event.key === 'Enter'); $('#renameProject').focus({ preventScroll: true });
  };
  input.focus(); input.select();
}
async function uploadFiles(files) {
  const projectId = state.project?.id;
  const canvasUpload = state.view === 'canvas';
  const videoId = canvasUpload && selectedNode()?.type === 'video' ? selectedNode().id : null;
  for (const file of files) {
    try {
      const form = new FormData(); form.append('file', file); if (state.project?.id) form.append('project_id', state.project.id);
      const asset = await api('/api/assets', { method: 'POST', body: form });
      state.assets.push(asset);
      const video = state.project?.id === projectId && state.nodes.find(node => node.id === videoId && node.type === 'video');
      if (video) selectAsset(asset.id, video);
      else if (canvasUpload && !videoId && state.project?.id === projectId) addAssetNode(asset, null);
      if (state.view === 'assets') renderAssetLibrary();
      toast(video ? `${file.name} 已添加素材节点并连接到视频` : `${file.name} 已加入生成历史`);
    } catch (error) { toast(error.message); }
  }
  if (!$('#canvasPanel').hidden && $('#canvasPanelTitle').textContent === '资产库') showAssetPanel();
}
function selectAsset(id, video = selectedNode()) {
  if (video?.type !== 'video' || !state.assets.some(asset => asset.id === id && ['image', 'prop','audio','video'].includes(asset.kind || 'image'))) return;
  if(video.settings?.video_mode==='first-last'){
    const asset=state.assets.find(item=>item.id===id);
    if(!['image','prop'].includes(asset.kind||'image'))return toast('首尾帧仅支持图片');
    video.settings.frame_asset_ids ||= [null,null];
    const index=video.settings.frame_asset_ids.indexOf(null);
    if(index>=0&&!video.settings.frame_asset_ids.includes(id)){video.settings.frame_asset_ids[index]=id;scheduleSave();canvasWorkspace?.refresh();}
  }
  if(state.edges.some(edge=>edge.to===video.id&&edge.relation!=='agent-flow'&&state.nodes.some(node=>node.id===edge.from&&referenceAssetId(node,state.characters)===id))) {renderAssets();return;}
  if (state.nodes.length >= 200 || state.edges.length >= 400) return toast('画布节点或连线已达上限，请先整理画布');
  video.settings = { ...video.settings, asset_ids: [...(video.settings?.asset_ids || []), id] };
  ensureAssetNodesForVideo(video);
  video.settings.mode = deriveVideoMode(video, state.nodes, state.edges, state.assets, state.characters);
  renderNodes(); loadNodeSettings();
  scheduleSave();
}
function unselectAsset(id) {
  const video = selectedNode();
  if (video?.type !== 'video') return;
  if(resolveVideoAssets(video,state.nodes,state.edges,state.assets,state.characters).entries.some(entry=>entry.asset.id===id&&entry.sources.some(source=>source.type==='prompt')))return toast('该素材由上游文本通过 @ 引用，请在上游文本中移除引用');
  if (resolveVideoAssets(video, state.nodes, state.edges, state.assets, state.characters).entries.some(entry => entry.asset.id === id && entry.sources.some(source => source.type === 'script'))) return toast('这张图片来自脚本资源，请在脚本的准备资源中移除关联');
  const sources = new Set(state.nodes.filter(node => ['asset', 'prop', 'character','audio','video-asset','video'].includes(node.type) && referenceAssetId(node, state.characters) === id).map(node => node.id));
  state.edges = state.edges.filter(edge => edge.to !== video.id || !sources.has(edge.from));
  video.settings.asset_ids = (video.settings.asset_ids || []).filter(assetId => assetId !== id);
  for(const mention of video.settings.mentions||[])if(mention.asset_id===id)video.settings.prompt=String(video.settings.prompt||'').split(mention.token).join('');
  video.settings.mentions=(video.settings.mentions||[]).filter(mention=>mention.asset_id!==id);
  video.settings.reference_order = (video.settings.reference_order || []).filter(assetId => assetId !== id);
  video.settings.mode = deriveVideoMode(video, state.nodes, state.edges, state.assets, state.characters);
  state.selectedEdgeIndex = null;
  renderEdges(); updatePortHighlights(); loadNodeSettings(); scheduleSave();
  if (!$('#canvasPanel').hidden && $('#canvasPanelTitle').textContent === '资产库') showAssetPanel();
  toast('已断开该素材与当前视频的连接，素材节点仍可复用');
}
function renderAssets() {
  const node = selectedNode();
  const video = node?.type === 'video' ? node : null;
  if (node?.type === 'video') state.mode = deriveVideoMode(video, state.nodes, state.edges, state.assets, state.characters);
  const references = resolveVideoAssets(video, state.nodes, state.edges, state.assets, state.characters);
  state.selectedAssetIds = references.assetIds;
  $('#assetRequirement').textContent = references.entries.length ? `${references.entries.length} 项参考素材 · 与画布连线同步` : '连接图片、视频或音频素材节点，将用于本镜头';
  $('#selectedAssets').innerHTML = references.entries.map((entry, index) => {
    const { asset, sources } = entry;
    return `<div class="reference-asset" data-reference-asset="${escapeHtml(asset.id)}" ${asset.kind==='audio'?`data-audio-preview="${escapeHtml(asset.url)}"`:''} title="${escapeHtml(asset.name)}"><span class="reference-number">${index+1}</span>${asset.kind==='audio'&&asset.duration?`<span class="reference-duration">${Number(asset.duration).toFixed(1)}s</span>`:''}${asset.kind==='audio'?`<span class="reference-audio-icon">♪</span>`:asset.kind==='video'?`<span class="reference-audio-icon">${icon('play')}</span>`:asset.url ? `<img src="${escapeHtml(asset.url)}" alt="">` : `<span>${icon('image','ui-icon-sm')}</span>`}<div><strong>参考 ${index + 1} · ${escapeHtml(asset.name || '素材')}</strong><small>${sources.length ? `来自：${sources.map(source => escapeHtml(source.title || source.characterName || '参考节点')).join('、')}` : '待同步到画布'}</small>${sources.map(source => `<button data-reference-source="${escapeHtml(source.id)}">查看来源 ${icon('arrowUpRight','ui-icon-sm')}</button>`).join('')}</div><div class="reference-actions">${index > 0 ? `<button data-reference-up="${escapeHtml(asset.id)}" title="向前移动参考素材" aria-label="向前移动参考图">${icon('arrowUp','ui-icon-sm')}</button>` : ''}<button data-remove-asset="${escapeHtml(asset.id)}" title="断开与当前视频的连接" aria-label="移除参考素材">${icon('close','ui-icon-sm')}</button></div></div>`;
  }).join('') + references.missing.map(item => `<div class="reference-missing">${escapeHtml(item.source?.title || '参考素材')}：${item.assetId ? '图片已失效' : '尚未绑定参考图'}${item.source ? `<button data-reference-source="${escapeHtml(item.source.id)}">查看来源 ↗</button>` : ''}</div>`).join('') || '<span class="asset-empty-hint">连接素材/角色节点，或上传图片作为参考</span>';
  $('#selectedAssets').querySelectorAll('[data-remove-asset]').forEach(button => button.onclick = () => unselectAsset(button.dataset.removeAsset));
  $('#selectedAssets').querySelectorAll('[data-reference-source]').forEach(button => button.onclick = () => updateSelection(button.dataset.referenceSource));
  $('#selectedAssets').querySelectorAll('[data-reference-up]').forEach(button => button.onclick = () => {
    if (node?.type !== 'video') return;
    const order = [...references.assetIds], index = order.indexOf(button.dataset.referenceUp);
    if (index < 1) return;
    [order[index - 1], order[index]] = [order[index], order[index - 1]];
    node.settings.reference_order = order; renderAssets(); scheduleSave();
  });
}
async function deleteAsset(id) { try { await api(`/api/assets/${id}`, { method: 'DELETE' }); state.assets = state.assets.filter(asset => asset.id !== id); state.selectedAssetIds = state.selectedAssetIds.filter(assetId => assetId !== id); state.nodes = state.nodes.map(node => node.assetId === id ? { ...node, assetId: '', assetUrl: '', assetName: '' } : node); renderAssets(); renderNodes(); renderAssetLibrary(); scheduleSave(); toast('素材已删除'); } catch (error) { toast(error.message); } }
function downloadTask(task) { if (!task?.video_url) return toast('该任务还没有可下载的视频'); const link = document.createElement('a'); link.href = task.video_url; link.download = `${(task.params?.prompt || '建绘通作品').slice(0, 18)}.mp4`; link.target = '_blank'; link.rel = 'noopener'; document.body.appendChild(link); link.click(); link.remove(); }
const escapeHtml = (value = '') => String(value).replace(/[&<>"']/g, char => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[char]));
async function refreshModelState() {
  const [models, llm] = await Promise.all([api('/api/models'), api('/api/llm')]);
  state.models = models; state.llm = llm;
  const video = selectedNode();
  state.selectedModelId = video?.type === 'video' && models.some(model => model.id === video.settings?.model_config_id)
    ? video.settings.model_config_id : models.find(model => model.is_default)?.id;
  renderModelSelect(); renderNodes(); loadNodeSettings();
}
const modelManager = createModelManager({ api, json, escapeHtml, toast, onChanged: refreshModelState });
async function openModelManagement(kind = 'text', options = {}) {
  const workflow = state.scriptWorkflow;
  if (workflow?.generating || workflow?.excelLoading || workflow?.importSaving) {
    toast('请等待当前脚本操作完成后再管理模型'); return;
  }
  const context = { view: state.view === 'models' ? 'canvas' : state.view || 'canvas',
    projectId: state.project?.id, workflow, onReturn: options.onReturn };
  try { await flushSave(); }
  catch (error) { toast(`请先保存当前项目：${error.message}`); await options.onReturn?.(); return; }
  if (state.modelReturn?.onReturn && !options.onReturn) { imageGenerator.close(); audioGenerator.close(); }
  if (!$('#modalBackdrop').hidden) closeModal();
  state.modelReturn = context; state.modelCategory = kind;
  showView('models');
}
async function returnFromModelManagement() {
  const context = state.modelReturn;
  try { await refreshModelState(); }
  catch (error) { toast(`模型配置刷新失败：${error.message}`); return; }
  state.modelReturn = null;
  showView(context?.view && context.view !== 'models' ? context.view : state.project ? 'canvas' : 'projects');
  if (context?.workflow && state.project?.id === context.projectId && scriptNodeById(context.workflow.nodeId)) {
    state.scriptWorkflow = context.workflow; renderScriptWorkflow();
  }
  await context?.onReturn?.();
}
const imageGenerator = createImageGenerator({ api, json, escapeHtml, toast,
  onSaveAsset: (asset,inLibrary)=>assetLibrary.setLibraryMembership(asset,inLibrary),
  onManageModels: options => openModelManagement('image', options),
  onAssetCreated: asset => {
    upsertAsset(asset);
    if (state.view === 'assets') assetLibrary.render();
  }
});
const audioGenerator = createAudioGenerator({ api, json, escapeHtml, toast,
  onSaveAsset: (asset,inLibrary)=>assetLibrary.setLibraryMembership(asset,inLibrary),
  onManageModels: options => openModelManagement('audio', options),
  onAssetCreated: asset => { upsertAsset(asset); if (state.view === 'assets') assetLibrary.render(); },
});
const inlineNodeMedia = createInlineNodeMedia({ state, api, json, escapeHtml, icon, toast,
  nodeAsset, upsertAsset, bindAsset: bindNodeAsset, selectedNode, select: updateSelection,
  save: scheduleSave, flushSave, manageModels: openModelManagement, selectVideoAsset: selectAsset,
  refresh: () => canvasWorkspace?.refresh(), showEditor: () => canvasWorkspace?.onSelect(),
  removeReferenceEdge: (from,to) => { state.edges=state.edges.filter(edge=>!(edge.from===from&&edge.to===to&&edge.relation!=='agent-flow'));renderEdges();scheduleSave(); }
});
function installCanvasMentions() {
  attachAssetMentions({root:document,selector:'#promptInput,#detailPromptInput,.inline-generation-prompt,[data-script-storyboard],[data-script-motion]',icon,escapeHtml,onError:toast,
    getLinkedReferences:input=>{
      const node=selectedNode();if(!node)return [];
      const inline=input.matches('.inline-generation-prompt');
      const owner=input.id==='promptInput'?resolveVideoInput(node,state.nodes,state.edges).source||node:node;
      const settings=inline?node[node.type==='audio'?'audio_settings':'image_settings']:owner.settings;
      const media=node.type==='video'?resolveVideoAssets(node,state.nodes,state.edges,state.assets,state.characters).entries.map(entry=>({assetId:entry.asset.id,nodeId:entry.sources[0]?.id})):[...(settings?.reference_asset_ids||[]).map(assetId=>({assetId})),...state.edges.filter(edge=>edge.to===node.id&&edge.relation!=='agent-flow').map(edge=>state.nodes.find(item=>item.id===edge.from)).filter(Boolean).map(source=>({nodeId:source.id,assetId:nodeAsset(source)?.id}))];
      const texts=state.edges.filter(edge=>edge.to===node.id&&edge.relation!=='agent-flow').map(edge=>state.nodes.find(item=>item.id===edge.from)).filter(item=>item?.type==='prompt').map(item=>({nodeId:item.id}));
      return [...media,...texts,...(settings?.mentions||[]).map(item=>({assetId:item.asset_id,nodeId:item.node_id}))];
    },
    getReferences:input=>{
      const node=selectedNode();if(!node)return [];
      const owner=input.id==='promptInput'?resolveVideoInput(node,state.nodes,state.edges).source||node:node;
      const settings=input.matches('.inline-generation-prompt')?node[node.type==='audio'?'audio_settings':'image_settings']:owner.settings;
      const ordered=node.type==='video'?resolveVideoAssets(node,state.nodes,state.edges,state.assets,state.characters).entries.map(entry=>entry.asset.id):settings?.reference_asset_ids||[];
      return (settings?.mentions||[]).map((mention,index)=>{
        const asset=state.assets.find(item=>item.id===mention.asset_id);
        return {token:mention.token,nodeId:mention.node_id,assetId:mention.asset_id,name:mention.name,kind:mention.kind,url:asset?.url,duration:asset?.duration,number:ordered.includes(mention.asset_id)?ordered.indexOf(mention.asset_id)+1:index+1};
      });
    },
    getItems:async()=>{
      state.assets=await api('/api/assets');
      const current=selectedNode(),seen=new Set(),items=[];
      for(const node of state.nodes) {
        if(node.id===current?.id)continue;
        const task=node.type==='video'?state.nodeTasks[node.id]:null;
        const asset=node.type==='video'?state.assets.find(item=>item.task_id===task?.id):nodeAsset(node);
        const kind=node.type==='character'?'character':node.type==='prompt'?'text':task?.status==='done'?'video':asset?.kind==='prop'?'image':asset?.kind;
        if(!kind||(!asset&&node.type!=='prompt'&&task?.status!=='done'))continue;
        if(asset)seen.add(asset.id);
        items.push({nodeId:node.id,assetId:asset?.id,name:node.characterName||node.title||asset?.name,kind,url:asset?.url||task?.video_url,asset});
      }
      for(const asset of state.assets)if(!seen.has(asset.id))items.push({assetId:asset.id,name:asset.name,kind:asset.kind==='prop'?'image':asset.kind||'image',url:asset.url,asset});
      for(const character of state.characters) {
        const asset=state.assets.find(item=>item.id===character.asset_id);
        if(asset&&!state.nodes.some(node=>node.type==='character'&&node.characterId===character.id))items.push({assetId:asset.id,name:character.name,kind:'character',url:asset.url,asset});
      }
      return items;
    },
    onSelect:async(item,input,token)=>{
      const node=selectedNode();if(!node)throw new Error('请先选择节点');

      const source=state.nodes.find(candidate=>candidate.id===item.nodeId);
      if(node.type==='video'&&item.kind==='video'&&source?.type==='video') {
        if(state.edges.length>=400&&!state.edges.some(edge=>edge.from===source.id&&edge.to===node.id))throw new Error('画布连线已达上限');
        item.asset=await prepareVideoReference(source);item.assetId=item.asset.id;
      }
      if(input.matches('.inline-generation-prompt')&&item.asset) {
        const allowed=node.type==='audio'?['image','prop','audio','text','script']:['image','prop','text','script'];
        if(!allowed.includes(item.asset.kind||'image'))throw new Error('该素材类型不适用于当前生成节点');
      }
      return ()=>{
        if(!state.nodes.includes(node))return;
        const key=input.matches('.inline-generation-prompt')?(node.type==='audio'?'audio_settings':'image_settings'):'settings';
        const settings=node[key]||{};
        node[key]={...settings,mentions:[...(settings.mentions||[]).filter(m=>m.token!==token),{token,asset_id:item.assetId||'',node_id:item.nodeId||'',name:item.name,kind:item.kind,content:['text','script'].includes(item.asset?.kind)?String(item.asset.content||item.asset.description||'').slice(0,4000):''}]};
        if(node.type==='video'&&item.kind==='video'&&source?.type==='video'&&!state.edges.some(edge=>edge.from===source.id&&edge.to===node.id)) {state.edges.push({from:source.id,to:node.id});renderEdges();}
        if(node.type==='video'&&item.asset&&['image','prop','audio','video'].includes(item.asset.kind||'image'))selectAsset(item.asset.id,node);
        else if(input.matches('.inline-generation-prompt'))inlineNodeMedia.mention(node,['image','prop','audio'].includes(item.asset?.kind)?item.asset:null);
        scheduleSave();
      };
    }
  });
}
const assetLibrary = createAssetLibrary({ state, api, json, escapeHtml, toast, showModal, closeModal,
  openCanvasPanel, closeCanvasPanel, onAddAsset: addLibraryAsset,
  onGenerateImage: options => imageGenerator.open(options),
  onGenerateAudio: options => audioGenerator.open(options),
  onAddCharacter: character => { if (!state.project) return toast('请先创建或打开一个项目'); showView('canvas'); addCharacterNode(character); },
  onChanged: () => {
    document.querySelectorAll('[data-history-save]').forEach(button=>{
      const asset=state.assets.find(item=>item.id===button.dataset.historySave);
      if(asset)button.innerHTML=icon(asset.in_library?'libraryRemove':'library')+(asset.in_library?'移出资产库':'保存至资产库');
    });
    let changed = false;
    for (const node of state.nodes.filter(item => item.type === 'character')) {
      const character = state.characters.find(item => item.id === node.characterId);
      if (!character) continue;
      const patch = { characterName: character.name, description: character.description || character.role, assetId: character.asset_id || '', assetUrl: state.assets.find(item => item.id === character.asset_id)?.url || '', tone: character.tone };
      if (Object.entries(patch).some(([key, value]) => node[key] !== value)) { Object.assign(node, patch); changed = true; }
    }
    changed = syncScriptResourcesToCanvas() || changed;
    renderNodes(); loadNodeSettings(); if (changed) scheduleSave();
  }
});
const projectLibrary = createProjectLibrary({ state, api, json, icon, escapeHtml, toast, showModal, closeModal, flushSave,
  renderRecent: renderProjects, onOpen: selectProject, onCreate: newProjectModal,
  onDuplicate: duplicateProject, onDelete: confirmDeleteProject, onRestore: restoreProject, onPurge: confirmPurgeProject
});
function openCanvasPanel(title, subtitle, body) {
  const panel = $('#canvasPanel');
  panel.dataset.kind = title === '添加节点' ? 'palette' : 'content';
  panel.setAttribute('role', title === '添加节点' ? 'menu' : 'region'); panel.setAttribute('aria-label', title);
  panel.style.left = ''; panel.style.top = ''; panel.style.bottom = ''; panel.style.transform = '';
  $('#paletteSearch')?.remove();
  $('#canvasPanelTitle').textContent = title; $('#canvasPanelSub').textContent = subtitle; $('#canvasPanelBody').innerHTML = body;
  $('#dockAddNode').setAttribute('aria-expanded', String(title === '添加节点'));
  setSurfaceVisible(panel, true, { key: title });
}
function closeCanvasPanel() {
  $('#dockAddNode').setAttribute('aria-expanded', 'false');
  const panel = $('#canvasPanel');
  setSurfaceVisible(panel, false).then(() => {
    if (!surfaceIsOpen(panel) && panel.hidden) { $('#canvasPanelBody').innerHTML = ''; $('#paletteSearch')?.remove(); }
  });
}
function showNodePalette(position = null) {
  state.pendingNodePosition = position && Number.isFinite(position.x) ? position : null;
  const types = [['prompt','text','文本'],['asset','image','图片'],['video','video','视频'],['audio','audio','音频'],['script','script','脚本'],['library','library','资产库']];
  openCanvasPanel('添加节点', '选择节点，连接你的创作', `<div class="node-palette">${types.map(([type, iconName, title]) => `<button class="palette-card palette-${type}" data-palette-node="${type}">${icon(iconName)}<div><b>${title}</b></div>${['script','library'].includes(type) ? icon('chevronRight','palette-chevron') : ''}</button>`).join('')}</div>`);
  document.querySelectorAll('[data-palette-node]').forEach(button => button.onclick = () => {
    const type = button.dataset.paletteNode;
    if (type === 'video') addVideoNode(); else if (type === 'script') addScriptNode(); else if (type === 'prompt') addPromptNode(); else if (type === 'character') showCharacterPanel(); else if (type === 'library') showAssetPanel();
    else appendNode(type, { title: {asset:'图片素材',prop:'道具素材','video-asset':'视频素材',audio:'音频'}[type], assetId: '' });
  });
  canvasWorkspace?.enhancePalette(state.pendingNodePosition);
}
function showAssetPanel() {
  const video = selectedNode()?.type === 'video' ? selectedNode() : null;
  if (video && state.view==='canvas') { inlineNodeMedia.pick(video,true); return; }
  const projectId = state.project?.id;
  assetLibrary.openPicker({ kind: video ? ['image','prop','video','audio'] : 'image', title: video ? '选择参考素材' : '图片资产库', onPick: asset => {
    if (state.project?.id !== projectId) return toast('项目已切换，请重新选择素材');
    if (video && state.nodes.includes(video)) selectAsset(asset.id, video); else addLibraryAsset(asset);
  } });
}
function showCharacterPanel() { assetLibrary.openCharacters(); }
function newCharacterModal() { assetLibrary.editCharacter(); }
function scriptNodeById(nodeId) { return state.nodes.find(node => node.id === nodeId && node.type === 'script'); }
function scriptDataForNode(node) {
  if (!node.script_data) node.script_data = emptyScriptData(node.sourceNodeId || '');
  for (const scene of node.script_data.scenes || []) {
    // Seed the editor only for final-only imports; motion-only prompts must not
    // be copied into the storyboard field and combined twice on the next save.
    if (!String(scene.storyboard_prompt || '').trim() && !String(scene.motion_prompt || '').trim()) {
      scene.storyboard_prompt = scriptScenePrompt(scene);
    }
    scene.final_prompt = scriptScenePrompt(scene);
  }
  return node.script_data;
}
// Keep the video settings produced from a storyboard in one place.  Storyboard
// data may come from the LLM, an Excel import, or a hand-edited row, so accept
// the field names used by each source and keep the safe platform defaults for
// older scripts which do not have video settings yet.
function scriptVideoSettings(data, scene) {
  const source = data?.video_settings || data?.videoSettings || {};
  const prompt = scriptScenePrompt(scene);
  const aspect_ratio = String(scene?.aspect_ratio || scene?.aspectRatio || source.aspect_ratio || source.aspectRatio || data?.aspect_ratio || data?.aspectRatio || '16:9');
  const resolution = String(scene?.resolution || source.resolution || data?.resolution || '768p').toLowerCase();
  const rawDuration = scene?.duration ?? source.duration ?? data?.video_duration ?? data?.videoDuration;
  const parsedDuration = Number.parseFloat(String(rawDuration ?? '').replace(/[，,]/g, '.'));
  const duration = Number.isFinite(parsedDuration) && parsedDuration > 0 ? Math.min(60, Math.max(1, Math.round(parsedDuration))) : 4;
  return { prompt, aspect_ratio, resolution, duration };
}
function scriptExpansionStatus(node) {
  const data = scriptDataForNode(node), scenes = Array.isArray(data.scenes) ? data.scenes : [];
  const resources = preparationResources(data);
  const missingResources = resources.filter(resource => {
    if (resource.required === false || String(resource.id || '').startsWith('resource-')) return false;
    const asset = state.assets.find(item => item.id === resource.asset_id);
    return !acceptsResourceAsset(resource, asset);
  });
  const missingPrompts = scenes.filter(scene => !scriptScenePrompt(scene));
  return {
    script: scenes.length > 0,
    resources: missingResources.length === 0,
    prompts: scenes.length > 0 && missingPrompts.length === 0,
    complete: scenes.length > 0 && missingResources.length === 0 && missingPrompts.length === 0,
    sceneCount: scenes.length,
    missingResources,
    missingPrompts,
  };
}
function scriptExpansionHint(status) {
  if (status.complete) return '三个步骤已完成，可以展开分镜到画布。';
  const missing = [];
  if (!status.script) missing.push('确认镜头');
  if (!status.resources) missing.push('准备必需资源');
  if (!status.prompts) missing.push('合成提示词');
  return `请先完成：${missing.join('、')}。完成后才能展开分镜到画布。`;
}
function scriptSourceText(node) {
  const data = scriptDataForNode(node);
  const textNodes = state.nodes.filter(item => item.type === 'prompt');
  const edgeSource = state.edges.find(edge => edge.to === node.id && edge.relation !== 'agent-flow' && textNodes.some(item => item.id === edge.from))?.from;
  const source = textNodes.find(item => item.id === data.source_node_id) || textNodes.find(item => item.id === edgeSource) || textNodes[0];
  return source?.settings?.prompt?.trim() || '';
}
function scriptCell(data, index, field, multiline = false) {
  const value = escapeHtml(data.scenes?.[index]?.[field] || '');
  return multiline ? `<textarea data-script-cell="${field}" data-scene-index="${index}" rows="3">${value}</textarea>` : `<input data-script-cell="${field}" data-scene-index="${index}" value="${value}">`;
}
const scriptExcelColumns = [['index', '镜号'], ['duration', '时长'], ['description', '画面描述'], ['shot_size', '景别'], ['lighting', '光影氛围'], ['dialogue', '对白·旁白'], ['sound', '音效'], ['camera', '运镜'], ['final_prompt', '最终提示词']];
function scriptExcelToolsHtml(data) {
  const workflow = state.scriptWorkflow;
  const busy = workflow.excelLoading || workflow.importSaving || workflow.generating;
  let preview = '';
  if (workflow.excelPreview) {
    const imported = workflow.excelPreview;
    const currentCount = data.scenes?.length || 0;
    preview = `<section class="script-import-preview" aria-label="Excel 导入预览"><div class="script-import-heading"><div><strong>导入预览 · ${imported.scenes.length} 个镜头</strong><p>${escapeHtml(imported.file_name)} · ${escapeHtml(imported.sheet_name)}</p></div><button class="script-secondary" id="scriptImportCancel" ${busy ? 'disabled' : ''}>取消导入</button></div>${(imported.warnings || []).map(message => `<p class="script-import-warning">${escapeHtml(message)}</p>`).join('')}<div class="script-import-table-wrap"><table class="script-import-table"><thead><tr>${scriptExcelColumns.map(([, label]) => `<th>${label}</th>`).join('')}</tr></thead><tbody>${imported.scenes.map(scene => `<tr>${scriptExcelColumns.map(([key]) => `<td>${escapeHtml(scene[key] ?? '')}</td>`).join('')}</tr>`).join('')}</tbody></table></div><div class="script-import-confirm"><p>${currentCount ? `当前已有 ${currentCount} 个镜头。追加会保留原分镜；替换会清空原镜头与资源的关联，已上传资源仍保留。` : '确认后导入分镜，可继续编辑、分析资源和合成提示词。'}</p><div>${currentCount ? `<button class="script-secondary" id="scriptImportReplace" ${busy ? 'disabled' : ''}>替换现有 ${currentCount} 个镜头</button>` : ''}<button class="script-primary" id="scriptImportAppend" ${busy || currentCount + imported.scenes.length > 80 ? 'disabled' : ''}>${currentCount ? '追加' : '导入'} ${imported.scenes.length} 个镜头</button></div></div>${currentCount + imported.scenes.length > 80 ? '<p class="script-import-warning">追加后超过 80 个镜头，请减少数量或选择替换。</p>' : ''}</section>`;
  }
  return `<div class="script-excel-toolbar"><div><strong>从 Excel 导入分镜</strong><small>使用模板表头 · .xlsx · 最大 5 MB · 最多 80 个镜头</small></div><div class="script-excel-actions"><a class="script-secondary" id="scriptExcelTemplate" href="${marketMediaUrl('/api/scripts/template')}" download="建绘通分镜脚本模板.xlsx">${icon('download','ui-icon-sm')}下载模板</a><button class="script-secondary" id="scriptExcelImportBtn" ${busy ? 'disabled' : ''}>${workflow.excelLoading ? '正在读取…' : `${icon('upload','ui-icon-sm')}导入 Excel`}</button><input type="file" id="scriptExcelFile" accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet" hidden></div></div>${workflow.excelError ? `<p class="script-import-error" role="alert">${escapeHtml(workflow.excelError)}</p>` : ''}${preview}`;
}
async function previewScriptExcel(file) {
  const workflow = state.scriptWorkflow, projectId = state.project?.id;
  const node = workflow && scriptNodeById(workflow.nodeId);
  if (!file || !node || workflow.excelLoading || workflow.importSaving || workflow.generating) return;
  workflow.excelPreview = null;
  workflow.excelError = '';
  if (!/\.xlsx$/i.test(file.name)) workflow.excelError = '请选择 .xlsx 文件；旧版 .xls 请先在 Excel 中另存为 .xlsx。';
  else if (file.size > 5 * 1024 * 1024) workflow.excelError = '文件超过 5 MB，请减少内容后再导入。';
  if (workflow.excelError) { renderScriptWorkflow(); return; }
  workflow.excelLoading = true;
  renderScriptWorkflow();
  try {
    const form = new FormData(); form.append('file', file); if (state.project?.id) form.append('project_id', state.project.id);
    const preview = await api('/api/scripts/import-excel', { method: 'POST', body: form });
    if (state.scriptWorkflow !== workflow || state.project?.id !== projectId || !state.nodes.includes(node)) return;
    if (!Array.isArray(preview.scenes) || !preview.scenes.length) throw new Error('表格中没有可导入的分镜，请检查模板内容。');
    workflow.excelPreview = preview;
  } catch (error) {
    if (state.scriptWorkflow === workflow) workflow.excelError = error.message;
  } finally {
    workflow.excelLoading = false;
    if (state.scriptWorkflow === workflow && state.project?.id === projectId) renderScriptWorkflow();
  }
}
async function applyScriptExcel(replace = false) {
  const workflow = state.scriptWorkflow;
  const node = workflow && scriptNodeById(workflow.nodeId);
  if (!node || !workflow.excelPreview || workflow.importSaving || workflow.generating) return;
  const imported = structuredClone(workflow.excelPreview.scenes);
  const data = scriptDataForNode(node);
  const scenes = replace ? imported : [...(data.scenes || []), ...imported];
  if (scenes.length > 80) return toast('一个脚本最多 80 个镜头，请减少导入数量或选择替换');
  data.scenes = scenes.map((scene, index) => ({ ...scene, index: index + 1 }));
  if (replace) data.resources = (data.resources || []).map(resource => ({ ...resource, scene_indices: [] }));
  workflow.importSaving = true;
  workflow.excelPreview = null;
  workflow.excelError = '';
  saveScriptLocal(node, data);
  renderNodes(); renderScriptWorkflow();
  try {
    await flushSave();
    if (state.scriptWorkflow === workflow) toast(`已导入 ${imported.length} 个镜头并保存`);
  } catch (error) {
    workflow.excelError = `镜头已加入当前编辑内容，但保存失败：${error.message}。请保留页面并点击项目保存重试。`;
  } finally {
    workflow.importSaving = false;
    if (state.scriptWorkflow === workflow) renderScriptWorkflow();
  }
}
function scriptProgress(data, step) {
  const status = scriptExpansionStatus({ script_data: data });
  const resources = preparationResources(data), required = resources.filter(resource => resource.required !== false && !String(resource.id || '').startsWith('resource-'));
  const counts = [`${data.scenes?.length || 0} 个镜头`, required.length ? `${required.length - status.missingResources.length}/${required.length} 必需资源` : '无需必需资源', `${(data.scenes?.length || 0) - status.missingPrompts.length}/${data.scenes?.length || 0} 已合成`];
  const complete = [status.script, status.resources, status.prompts];
  return `<div class="script-progress">${['确认镜头', '准备资源', '合成提示词'].map((label, index) => `<button class="script-progress-step ${step === index + 1 ? 'active' : ''} ${complete[index] ? 'complete' : ''}" data-script-step="${index + 1}"><span>${complete[index] ? icon('check','ui-icon-sm') : index + 1}</span><b>${label}</b><small>${counts[index]}</small></button>${index < 2 ? '<i></i>' : ''}`).join('')}</div>`;
}
function scriptWorkflowHtml(node, data, step) {
  const source = scriptSourceText(node);
  const llmStatus = state.llm?.api_key_set ? `已配置 · ${state.llm.model_id}` : '未配置 API Key';
  const completion = scriptExpansionStatus(node);
  const actionError = state.scriptWorkflow?.error;
  const errorLabels = { script: '分镜生成', resources: '资源分析', prompts: '提示词合成' };
  const errorBanner = actionError ? `<div class="script-import-error script-action-error" role="alert"><strong>${escapeHtml(errorLabels[actionError.action] || '脚本处理')}未完成</strong><span>${escapeHtml(actionError.message)}</span><button class="script-secondary" id="scriptRetryAction">重新尝试</button></div>` : '';
  let content = '';
  if (step === 1) {
    const rows = data.scenes?.length ? data.scenes.map((scene, index) => `<tr><td class="scene-index">${index + 1}</td><td>${scriptCell(data, index, 'duration')}</td><td>${scriptCell(data, index, 'description', true)}</td><td>${scriptCell(data, index, 'shot_size')}</td><td>${scriptCell(data, index, 'lighting', true)}</td><td>${scriptCell(data, index, 'dialogue', true)}</td><td>${scriptCell(data, index, 'sound', true)}</td><td>${scriptCell(data, index, 'camera', true)}</td><td class="prompt-pending">${data.scenes[index].final_prompt ? '已生成' : '待生成'}</td><td><button class="script-row-delete" data-script-delete-scene="${index}" title="删除镜头">${icon('close','ui-icon-sm')}</button></td></tr>`).join('') : `<tr><td colspan="10" class="script-table-empty"><span>${icon('script','ui-icon-lg')}</span><b>还没有分镜内容</b><small>可在上方导入 Excel，或连接文本节点后让大模型生成分镜</small></td></tr>`;
    content = `<div class="script-source-card"><span>文本节点内容</span><p>${escapeHtml(source || '')}</p></div><div class="script-meta-fields"><label>脚本名称<input data-script-meta="title" value="${escapeHtml(data.title || '')}"></label><label>类型<input data-script-meta="genre" value="${escapeHtml(data.genre || '')}"></label><label>预计时长<input data-script-meta="duration" value="${escapeHtml(data.duration || '')}"></label><label>整体风格<input data-script-meta="style" value="${escapeHtml(data.style || '')}"></label></div><div class="script-table-wrap"><table class="script-table"><thead><tr><th>镜号</th><th>时长</th><th>画面描述</th><th>景别</th><th>光影氛围</th><th>对白·旁白</th><th>音效</th><th>运镜</th><th>最终提示词</th><th>操作</th></tr></thead><tbody>${rows}</tbody></table></div><div class="script-step-actions"><button class="script-secondary" id="scriptAddScene">${icon('plus','ui-icon-sm')}添加镜头</button><button class="script-primary" id="scriptGenerateScenes">${icon('wand','ui-icon-sm')}根据文本生成分镜</button></div>`;
    content = scriptExcelToolsHtml(data) + content;
  } else if (step === 2) {
    const filter = PREPARATION_TYPES[state.scriptWorkflow.resourceFilter] ? state.scriptWorkflow.resourceFilter : 'all';
    const resources = preparationResources(data);
    const tabs = `<div class="script-resource-tabs" role="tablist" aria-label="资源类型"><button data-resource-filter="all" class="${filter === 'all' ? 'active' : ''}" aria-selected="${filter === 'all'}">${icon('grid','ui-icon-sm')}全部 <b>${resources.length}</b></button>${Object.entries(PREPARATION_TYPES).map(([kind, info]) => `<button data-resource-filter="${kind}" class="${filter === kind ? 'active' : ''}" aria-selected="${filter === kind}">${icon(info.icon,'ui-icon-sm')}${info.label} <b>${resources.filter(resource => resourceKind(resource) === kind).length}</b></button>`).join('')}</div>`;
    const groups = Object.entries(PREPARATION_TYPES).filter(([kind]) => filter === 'all' || filter === kind).map(([kind, info]) => {
      const items = resources.filter(resource => resourceKind(resource) === kind);
      if (!items.length) return '';
      return `<section class="script-resource-group" data-resource-group="${kind}"><div class="script-resource-group-head"><h3>${icon(info.icon,'ui-icon-sm')}${info.label}资源 <span>${items.filter(resource => state.assets.some(asset => asset.id === resource.asset_id && acceptsResourceAsset(resource, asset))).length}/${items.length} 已准备</span></h3><small>${info.hint}</small></div><div class="script-resource-list">${items.map(resource => {
        const asset = state.assets.find(item => item.id === resource.asset_id);
        const ready = acceptsResourceAsset(resource, asset);
        const visual = ready && kind === 'image' ? `<img src="${escapeHtml(asset.url)}" alt="${escapeHtml(asset.name)}">` : icon(info.icon);
        const optional = resource.required === false || String(resource.id || '').startsWith('resource-');
        return `<article class="script-resource-card ${ready ? 'resource-ready' : ''}" data-resource-kind="${kind}"><div class="resource-mark">${visual}</div><div class="resource-info"><strong>${escapeHtml(resource.name)}</strong><span>${escapeHtml(resource.category || info.label)} · ${optional ? '可选' : '建议准备'}</span><p>${escapeHtml(resource.reason || '')}</p>${ready ? `<small class="resource-uploaded">已关联：${escapeHtml(asset.name)}</small>` : resource.asset_id ? '<small class="resource-type-error">已关联素材类型不符或文件失效，请重新选择。</small>' : `<small class="resource-format">请上传${info.label}文件，也可从资产库复用</small>`}</div><div class="script-resource-buttons">${kind === 'image' ? `<button class="script-resource-generate" data-script-generate-image="${escapeHtml(resource.id)}">${icon('sparkle','ui-icon-sm')}AI 生成图片</button>` : ''}<button class="script-resource-upload ${ready ? 'uploaded' : ''}" data-script-upload-resource="${escapeHtml(resource.id)}">${ready ? '更换' : '上传'}${info.label}</button><button class="script-resource-library" data-script-pick-resource="${escapeHtml(resource.id)}">从${info.label}库选择</button>${resource.asset_id ? `<button class="script-resource-library" data-script-unbind="${escapeHtml(resource.id)}">移除关联</button>` : ''}</div></article>`;
      }).join('')}</div></section>`;
    }).join('');
    content = `<div class="script-step-intro"><div><h3>根据分镜分类准备资源</h3><p>人物、场景和道具准备图片，片段素材准备视频，旁白、配乐和音效准备音频。图片支持 AI 生成，也可上传或从资产库复用。已关联的资源会自动显示为画布素材节点，并连接到当前脚本。</p></div><button class="script-secondary" id="scriptGenerateResources">重新分析资源</button></div>${tabs}${groups || `<div class="script-workflow-empty">${icon(RESOURCE_TYPES[filter]?.icon || 'diamond')}<b>${resources.length ? '此类型暂无资源需求' : '还没有资源清单'}</b><small>${resources.length ? '可切换其他分类查看需要准备的素材' : '先完成或导入 Excel，再点击「重新分析资源」'}</small></div>`}`;
  } else {
    const prompts = data.scenes?.length ? data.scenes.map((scene, index) => `<article class="script-prompt-card"><div class="script-prompt-title"><span>${index + 1}</span><div><strong>第 ${index + 1} 镜 · ${escapeHtml(scene.shot_size || '中景')}</strong><small>${escapeHtml(scene.duration || '')} · ${escapeHtml(scene.description || '').slice(0, 80)}</small></div><button class="script-mini-generate" data-script-generate-prompt="${index}">${icon('wand','ui-icon-sm')} 重新生成</button></div><label>分镜提示词<textarea data-script-storyboard="${index}" rows="4" placeholder="画面主体、构图、光影和风格">${escapeHtml(scene.storyboard_prompt || '')}</textarea></label><label>视频运动提示词<textarea data-script-motion="${index}" rows="3" placeholder="人物动作、镜头运动、速度和转场">${escapeHtml(scene.motion_prompt || '')}</textarea></label><span class="autosave-label">自动保存</span></article>`).join('') : `<div class="script-workflow-empty"><span>${icon('script','ui-icon-lg')}</span><b>还没有分镜</b><small>返回第一步生成分镜内容</small></div>`;
    content = `<div class="script-step-intro"><div><h3>为每个镜头合成视频提示词</h3><p>分镜提示词描述画面，运动提示词描述动作与运镜。展开后的视频节点直接使用这两项原文组合的提示词，修改后同步生效。</p></div><button class="script-primary" id="scriptGeneratePrompts">${icon('wand','ui-icon-sm')} 合成全部提示词</button></div><div class="script-prompt-list">${prompts}</div>`;
  }
  const expandButton = `<button class="script-primary script-expand-canvas" id="scriptExpandCanvas" ${completion.complete ? '' : 'disabled'}>${completion.complete ? `展开分镜到画布 ${icon('arrowUpRight','ui-icon-sm')}` : '完成三步后展开分镜'}</button>`;
  return `<div class="script-workflow"><header class="script-workflow-head"><div><span class="eyebrow">脚本节点</span><h2>${escapeHtml(data.title || '新建分镜脚本')}</h2><p>导入 Excel 或从文本创意生成可编辑的分镜脚本</p></div><div class="script-workflow-actions"><span class="llm-connection ${state.llm?.api_key_set ? 'connected' : ''}">${icon('link','ui-icon-sm')} ${escapeHtml(llmStatus)}</span><button class="script-secondary" id="scriptLlmSettings">大模型设置</button><button class="script-close" id="scriptWorkflowClose" aria-label="关闭">${icon('close','ui-icon-sm')}</button></div></header>${scriptProgress(data, step)}<main class="script-workflow-main">${errorBanner}${content}</main><footer class="script-workflow-foot"><span>${completion.complete ? '三个步骤已完成，可将分镜展开为视频生成节点。' : escapeHtml(scriptExpansionHint(completion))}</span><div>${expandButton}<button class="script-secondary" id="scriptPrev" ${step === 1 ? 'disabled' : ''}>${icon('chevronLeft','ui-icon-sm')} 上一步</button><button class="script-primary" id="scriptNext" ${step === 3 ? 'disabled' : ''}>下一步 ${icon('chevronRight','ui-icon-sm')}</button></div></footer></div>`;
}
function openScriptWorkflow(nodeId, step = 1) {
  const node = scriptNodeById(nodeId);
  if (!node) return;
  state.selectedNodeId = nodeId;
  state.selectedNodeIds = new Set([nodeId]);
  state.scriptWorkflow = { nodeId, step, projectId: state.project?.id };
  if(state.view==='canvas') {updateSelection(nodeId);canvasWorkspace?.onSelect();}
  renderScriptWorkflow();
}
function syncScriptResourcesToCanvas() {
  const result = syncScriptResourceGraph(state.nodes, state.edges, state.assets);
  if (result.changed) state.selectedEdgeIndex = null;
  const skipped = result.skippedBindings?.length || 0;
  if (skipped && state.scriptResourceLimitWarning !== skipped) toast(`资源已保留，${skipped} 项资源暂未展开到画布：节点或连线已达上限，请整理画布后重新打开项目`);
  state.scriptResourceLimitWarning = skipped;
  return result.changed;
}
function saveScriptLocal(node, data) {
  node.script_data = structuredClone(data);
  if (syncScriptResourcesToCanvas()) { renderNodes(); loadNodeSettings(); }
  scheduleSave();
}
function bindScriptWorkflow() {
  const workflow = state.scriptWorkflow;
  if (!workflow) return;
  const node = scriptNodeById(workflow.nodeId);
  if (!node) return closeModal();
  const data = scriptDataForNode(node);
  $('#scriptExcelImportBtn')?.addEventListener('click', () => $('#scriptExcelFile').click());
  $('#scriptExcelFile')?.addEventListener('change', event => previewScriptExcel(event.target.files[0]));
  $('#scriptImportCancel')?.addEventListener('click', () => { workflow.excelPreview = null; workflow.excelError = ''; renderScriptWorkflow(); });
  $('#scriptImportAppend')?.addEventListener('click', () => applyScriptExcel(false));
  $('#scriptImportReplace')?.addEventListener('click', () => applyScriptExcel(true));
  document.querySelectorAll('[data-script-step]').forEach(button => button.onclick = () => { workflow.step = Number(button.dataset.scriptStep); renderScriptWorkflow(); });
  $('#scriptWorkflowClose').onclick = () => { closeModal(); renderNodes(); loadNodeSettings(); };
  $('#scriptExpandCanvas')?.addEventListener('click', () => {
    const status = scriptExpansionStatus(node);
    if (!status.complete) return toast(scriptExpansionHint(status));
    closeModal();
    expandScriptShots(node);
  });
  $('#scriptLlmSettings').onclick = llmConfigModal;
  $('#scriptRetryAction')?.addEventListener('click', () => {
    const failed = workflow.error;
    workflow.error = null;
    if (failed) generateScriptAction(failed.action, failed.sceneIndex);
  });
  $('#scriptPrev').onclick = () => { if (workflow.step > 1) { workflow.step -= 1; renderScriptWorkflow(); } };
  $('#scriptNext').onclick = () => { if (workflow.step < 3) { workflow.step += 1; renderScriptWorkflow(); } };
  document.querySelectorAll('[data-script-meta]').forEach(input => input.oninput = () => { data[input.dataset.scriptMeta] = input.value; saveScriptLocal(node, data); });
  document.querySelectorAll('[data-script-cell]').forEach(input => input.oninput = () => { const index = Number(input.dataset.sceneIndex); if (data.scenes[index]) { data.scenes[index][input.dataset.scriptCell] = input.value; saveScriptLocal(node, data); } });
  const savePromptPart = (index, field, value) => {
    const scene = data.scenes[index];
    if (!scene) return;
    scene[field] = value;
    // Use just the editable parts here so clearing both fields cannot restore
    // an older final_prompt or a legacy prompt from before the edit.
    scene.final_prompt = scriptScenePrompt({ storyboard_prompt: scene.storyboard_prompt, motion_prompt: scene.motion_prompt });
    delete scene.prompt;
    saveScriptLocal(node, data);
  };
  document.querySelectorAll('[data-script-storyboard]').forEach(input => input.oninput = () => savePromptPart(Number(input.dataset.scriptStoryboard), 'storyboard_prompt', input.value));
  document.querySelectorAll('[data-script-motion]').forEach(input => input.oninput = () => savePromptPart(Number(input.dataset.scriptMotion), 'motion_prompt', input.value));
  document.querySelectorAll('[data-script-delete-scene]').forEach(button => button.onclick = () => { data.scenes.splice(Number(button.dataset.scriptDeleteScene), 1); data.scenes.forEach((scene, index) => { scene.index = index + 1; }); saveScriptLocal(node, data); renderScriptWorkflow(); });
  if ($('#scriptAddScene')) $('#scriptAddScene').disabled = data.scenes.length >= 80;
  $('#scriptAddScene')?.addEventListener('click', () => { if (data.scenes.length >= 80) return toast('一个脚本最多 80 个镜头'); data.scenes.push({ index: data.scenes.length + 1, duration: '6s', description: '', shot_size: '中景', lighting: '', dialogue: '+', sound: '', camera: '', final_prompt: '' }); saveScriptLocal(node, data); renderScriptWorkflow(); });
  $('#scriptGenerateScenes')?.addEventListener('click', () => generateScriptAction('script'));
  $('#scriptGenerateResources')?.addEventListener('click', () => generateScriptAction('resources'));
  $('#scriptGeneratePrompts')?.addEventListener('click', () => generateScriptAction('prompts'));
  document.querySelectorAll('[data-script-generate-prompt]').forEach(button => button.onclick = () => generateScriptAction('prompts', Number(button.dataset.scriptGeneratePrompt) + 1));
  document.querySelectorAll('[data-script-unbind]').forEach(button => button.onclick = () => { const resource = scriptDataForNode(node).resources.find(item => item.id === button.dataset.scriptUnbind); if (!resource) return; Object.assign(resource, { asset_id: '', file_name: '', status: 'pending' }); saveScriptLocal(node, scriptDataForNode(node)); renderScriptWorkflow(); });
  document.querySelectorAll('[data-resource-filter]').forEach(button => button.onclick = () => { workflow.resourceFilter = button.dataset.resourceFilter; renderScriptWorkflow(); });
  document.querySelectorAll('[data-script-upload-resource]').forEach(button => button.onclick = () => pickScriptResourceFile(button.dataset.scriptUploadResource));
  document.querySelectorAll('[data-script-pick-resource]').forEach(button => button.onclick = () => chooseScriptResourceFromLibrary(button.dataset.scriptPickResource));
  document.querySelectorAll('[data-script-generate-image]').forEach(button => button.onclick = () => generateScriptResourceImage(button.dataset.scriptGenerateImage));
}
function renderScriptWorkflow() {
  const workflow = state.scriptWorkflow;
  const node = workflow && scriptNodeById(workflow.nodeId);
  if (!workflow || !node) return closeModal();
  if(state.view==='canvas') {
    if(selectedNode()?.id!==node.id) return;
    const view=$('#nodeDetailView');
    view.innerHTML=`<div class="inline-script-workflow">${scriptWorkflowHtml(node,scriptDataForNode(node),workflow.step)}</div>`;
    canvasWorkspace?.refresh();
  } else showModal(scriptWorkflowHtml(node, scriptDataForNode(node), workflow.step), 'script-modal');
  bindScriptWorkflow();
}
async function generateScriptAction(action, sceneIndex = null) {
  const workflow = state.scriptWorkflow;
  const node = workflow && scriptNodeById(workflow.nodeId);
  if (!workflow || !node) return;
  if (workflow.excelLoading || workflow.importSaving || workflow.generating) return toast('请等待当前操作完成');
  if (workflow.excelPreview) return toast('请先确认或取消 Excel 导入预览');
  workflow.error = null;
  workflow.generating = true;
  const importButton = $('#scriptExcelImportBtn');
  if (importButton) importButton.disabled = true;
  const button = document.querySelector(action === 'script' ? '#scriptGenerateScenes' : action === 'resources' ? '#scriptGenerateResources' : '#scriptGeneratePrompts');
  if (button) { button.disabled = true; button.textContent = '大模型处理中…'; }
  try {
    await flushSave();
    const result = await api('/api/scripts/generate', json({ project_id: state.project.id, node_id: node.id, action, source_text: scriptSourceText(node), script_data: scriptDataForNode(node), revision: state.project.revision, scene_index: sceneIndex }));
    node.script_data = result.script_data;
    state.project.revision = result.project_revision;
    state.project.updated_at = result.updated_at;
    state.dirty = false;
    if (syncScriptResourcesToCanvas()) scheduleSave();
    if (state.llm) state.llm.api_key_set = true;
    renderNodes();
    renderScriptWorkflow();
    toast(action === 'script' ? '分镜脚本已生成' : action === 'resources' ? '资源清单已更新' : '镜头提示词已合成');
  } catch (error) {
    const message = scriptGenerationErrorMessage(error);
    workflow.error = { message, action, sceneIndex };
    toast(message);
    if (!state.llm?.api_key_set && /api.?key|大模型/i.test(String(error?.message || ''))) llmConfigModal();
    if (button) { button.disabled = false; button.innerHTML = action === 'script' ? `${icon('wand','ui-icon-sm')} 根据文本生成分镜` : action === 'resources' ? '重新分析资源' : `${icon('wand','ui-icon-sm')} 合成全部提示词`; }
  } finally {
    workflow.generating = false;
    if (state.scriptWorkflow === workflow) {
      if (workflow.error) renderScriptWorkflow();
      if ($('#scriptExcelImportBtn')) $('#scriptExcelImportBtn').disabled = false;
    }
  }
}
function scriptGenerationErrorMessage(error) {
  const raw = String(error?.message || error || '').trim();
  if (/read operation timed out|timed out|timeout|超时|deadline/i.test(raw)) {
    return '大模型响应超时，提示词尚未更新。当前脚本内容已保留，请稍后点击“重新尝试”再次提交。';
  }
  if (/rate.?limit|too many requests|429|限流|请求过多/i.test(raw)) {
    return '大模型当前请求较多，提示词尚未更新。当前脚本内容已保留，请稍后点击“重新尝试”。';
  }
  if (/api.?key|unauthorized|forbidden|401|403|大模型未配置/i.test(raw)) {
    return '脚本大模型未连接或没有权限。请打开“大模型设置”检查 API Key，当前脚本内容已保留。';
  }
  return `提示词合成失败：${raw || '服务暂时不可用'}。当前脚本内容已保留，请点击“重新尝试”。`;
}
async function uploadScriptResource(resourceId, files, expectedWorkflow = state.scriptWorkflow) {
  const workflow = expectedWorkflow, projectId = workflow?.projectId || state.project?.id;
  if (state.project?.id !== projectId) return toast('项目已切换，请重新上传');
  const node = workflow && scriptNodeById(workflow.nodeId);
  const resource = node && scriptDataForNode(node).resources.find(item => item.id === resourceId);
  if (!node || !resource || !files.length) return;
  for (const file of files.slice(0, 1)) {
    try {
      const form = new FormData(); form.append('file', file); if (state.project?.id) form.append('project_id', state.project.id);
      const asset = await api('/api/assets', { method: 'POST', body: form });
      upsertAsset(asset);
      if (!acceptsResourceAsset(resource, asset)) { toast(`这项资源需要${RESOURCE_TYPES[resourceKind(resource)].label}文件，所选文件已加入生成历史但未绑定`); continue; }
      const currentResource = state.project?.id === projectId && state.nodes.includes(node) && scriptDataForNode(node).resources.find(item => item.id === resourceId);
      if (!currentResource) { toast(`${file.name} 已加入生成历史，原脚本资源已变更`); continue; }
      if (!acceptsResourceAsset(currentResource, asset)) { toast('资源类型已变更，请从资产库重新选择'); continue; }
      currentResource.kind = resourceKind(currentResource); currentResource.asset_id = asset.id; currentResource.file_name = asset.name; currentResource.status = 'uploaded'; saveScriptLocal(node, scriptDataForNode(node));
      toast(`${file.name} 已关联到资源：${currentResource.name}，并连接到画布脚本节点`);
    } catch (error) { toast(error.message); }
  }
  if (state.scriptWorkflow === workflow) renderScriptWorkflow();
}
function pickScriptResourceFile(resourceId) {
  const workflow = state.scriptWorkflow;
  const resource = workflow && scriptDataForNode(scriptNodeById(workflow.nodeId)).resources.find(item => item.id === resourceId);
  if (!resource) return;
  const input = document.createElement('input'); input.type = 'file'; input.accept = RESOURCE_TYPES[resourceKind(resource)].accept;
  input.onchange = () => uploadScriptResource(resourceId, [...input.files], workflow);
  input.click();
}
function generateScriptResourceImage(resourceId) {
  const workflow = state.scriptWorkflow, node = workflow && scriptNodeById(workflow.nodeId);
  const data = node && scriptDataForNode(node);
  const resource = data?.resources.find(item => item.id === resourceId);
  if (!resource || resourceKind(resource) !== 'image') return;
  const projectId = state.project?.id;
  const scenes = (data.scenes || []).filter(scene => (resource.scene_indices || []).includes(scene.index) || (scene.resource_ids || []).includes(resource.id));
  const prompt = [`生成用于视频分镜的${resource.category || '参考'}图片：${resource.name}`, resource.reason,
    data.style ? `整体风格：${data.style}` : '',
    ...scenes.slice(0, 3).map(scene => `第 ${scene.index} 镜：${scene.description || ''}${scene.lighting ? '；光影：' + scene.lighting : ''}`)
  ].filter(Boolean).join('\n').slice(0, 4000);
  if(state.view==='canvas') {
    let target=state.nodes.find(item=>item.scriptResourceTarget?.nodeId===node.id&&item.scriptResourceTarget?.resourceId===resource.id);
    if(!target) target=appendNode('asset',{title:resource.name,x:node.x+400,y:node.y,image_settings:{prompt,name:resource.name},scriptResourceTarget:{nodeId:node.id,resourceId:resource.id}});
    if(target) inlineNodeMedia.focus(target);
    return;
  }
  imageGenerator.open({ project_id:projectId,name:resource.name,prompt,onUse:async asset=>{
    if(state.project?.id!==projectId||!state.nodes.includes(node))return;
    Object.assign(resource,{kind:'image',asset_id:asset.id,file_name:asset.name,status:'uploaded'});
    saveScriptLocal(node,scriptDataForNode(node));await flushSave();renderScriptWorkflow();
  }});
}
function chooseScriptResourceFromLibrary(resourceId) {
  const workflow = state.scriptWorkflow, node = workflow && scriptNodeById(workflow.nodeId);
  const resource = node && scriptDataForNode(node).resources.find(item => item.id === resourceId);
  if (!resource) return;
  const info = RESOURCE_TYPES[resourceKind(resource)];
  if(state.view==='canvas') {
    inlineNodeMedia.library($('#nodeDetailView'),{kind:resourceKind(resource)==='image'?['image','prop']:resourceKind(resource),title:`为「${resource.name}」添加素材`,onPick:asset=>{
      if(!acceptsResourceAsset(resource,asset))throw new Error('素材类型与资源不匹配');
      Object.assign(resource,{asset_id:asset.id,file_name:asset.name,status:'uploaded'});
      saveScriptLocal(node,scriptDataForNode(node));renderNodes();loadNodeSettings();
    }});return;
  }
  const draw = () => {
    const assets = state.assets.filter(asset => acceptsResourceAsset(resource, asset));
    showModal(`<h2>${icon('image','ui-icon-sm')}选择${info.label}资源</h2><p>为「${escapeHtml(resource.name)}」绑定已有${info.label}素材</p><input id="scriptResourceSearch" placeholder="搜索${info.label}名称或标签" aria-label="搜索同类资源"><div class="script-resource-picker">${assets.map(asset => `<button class="script-resource-pick" data-choose-script-asset="${escapeHtml(asset.id)}" data-search="${escapeHtml([asset.name, ...(asset.tags || [])].join(' ').toLowerCase())}">${asset.kind === 'image' || asset.kind === 'prop' || !asset.kind ? `<img src="${escapeHtml(asset.url)}" alt="">` : `<span>${icon(info.icon,'ui-icon-sm')}</span>`}<b>${escapeHtml(asset.name)}</b><small>选择</small></button>`).join('')}<p id="scriptResourceEmpty" ${assets.length ? 'hidden' : ''}>没有匹配的${info.label}素材，请返回后上传。</p></div><div class="modal-actions"><button class="secondary" id="scriptResourceBack">返回准备资源</button></div>`, 'script-resource-picker-modal');
    $('#scriptResourceBack').onclick = renderScriptWorkflow;
    $('#scriptResourceSearch').oninput = event => { const query = event.target.value.trim().toLowerCase(); let count = 0; document.querySelectorAll('[data-choose-script-asset]').forEach(button => { button.hidden = !button.dataset.search.includes(query); if (!button.hidden) count++; }); $('#scriptResourceEmpty').hidden = Boolean(count); };
    document.querySelectorAll('[data-choose-script-asset]').forEach(button => button.onclick = () => {
      const current = state.scriptWorkflow === workflow && state.nodes.includes(node) && scriptDataForNode(node).resources.find(item => item.id === resourceId);
      const asset = state.assets.find(item => item.id === button.dataset.chooseScriptAsset);
      if (!current || !acceptsResourceAsset(current, asset)) return toast('资源已变更，请返回重新选择');
      Object.assign(current, { kind: resourceKind(current), asset_id: asset.id, file_name: asset.name, status: 'uploaded' });
      saveScriptLocal(node, scriptDataForNode(node)); renderScriptWorkflow(); toast('已从资产库关联资源，并连接到画布脚本节点');
    });
  };
  draw();
}
function llmConfigModal() { return openModelManagement('text'); }

async function showCanvasHistoryPanel() {
  const projectId = state.project?.id;
  openCanvasPanel('生成历史', '查看生成和导入的内容，可添加到画布或保存至资产库', '<div class="panel-empty"><b>正在读取历史…</b></div>');
  const requestId = crypto.randomUUID();
  $('#canvasPanel').dataset.request = requestId;
  const current = () => state.project?.id === projectId && !$('#canvasPanel').hidden && $('#canvasPanel').dataset.request === requestId && $('#canvasPanelTitle').textContent === '生成历史';
  try {
    const [tasks, history] = await Promise.all([projectId ? api(`/api/tasks?project_id=${projectId}`) : [], api('/api/assets')]);
    if (!current()) return;
    history.forEach(upsertAsset);
    const items=history.filter(asset=>asset.project_id===projectId);
    const pendingTasks=tasks.filter(task=>!items.some(asset=>asset.task_id===task.id));
    $('#canvasPanelBody').innerHTML = items.length||pendingTasks.length ? `<div class="panel-list">${items.map(asset=>`<div class="history-panel-card" data-history-asset="${escapeHtml(asset.id)}"><b>${escapeHtml(asset.name)}</b><small>${escapeHtml({image:'图片',prop:'图片',video:'视频',audio:'音频'}[asset.kind]||'素材')} · ${asset.source==='generated'?'AI 生成':'导入'}${asset.file_status==='failed'?' · 文件下载失败':''}</small><button type="button" data-history-preview="${escapeHtml(asset.id)}">预览</button><button type="button" class="history-add-node" data-history-add="${escapeHtml(asset.id)}">添加到画布</button><button type="button" data-history-save="${escapeHtml(asset.id)}" >${icon(asset.in_library?'libraryRemove':'library')}${asset.in_library?'移出资产库':'保存至资产库'}</button>${asset.file_status==='failed'?`<button type="button" data-history-retry="${escapeHtml(asset.id)}">重试下载</button>`:''}</div>`).join('')}${pendingTasks.map(task => {
      const prompt = task.params?.prompt || '生成视频';
      return `<div class="history-panel-card"><b>${escapeHtml(prompt.slice(0, 36))}${prompt.length > 36 ? '…' : ''}</b><small>${escapeHtml(task.params?.model_name || task.params?.model || '视频模型')} · ${escapeHtml(task.params?.resolution || '')} · ${Number(task.params?.duration) || 0}s</small><span class="task-status ${escapeHtml(task.status)}">${statusText(task.status, task)}</span>${taskErrorHtml(task)}${taskRetryHtml(task)}${task.status === 'done' && task.video_url ? `<button class="history-add-node" data-history-add="${escapeHtml(task.id)}">${icon('plus','ui-icon-sm')}添加到画布</button>` : ''}</div>`;
    }).join('')}</div>` : '<div class="panel-empty"><b>还没有生成或导入内容</b><small>图片、视频和音频会保留在这里</small></div>';
    const findAsset=id=>items.find(asset=>asset.id===id);
    document.querySelectorAll('[data-history-preview]').forEach(button=>button.onclick=()=>assetLibrary.preview(findAsset(button.dataset.historyPreview)));
    document.querySelectorAll('[data-history-save]').forEach(button=>button.onclick=async()=>{button.disabled=true;await assetLibrary.setLibraryMembership(findAsset(button.dataset.historySave));if(button.isConnected){const asset=findAsset(button.dataset.historySave);button.disabled=false;button.innerHTML=icon(asset?.in_library?'libraryRemove':'library')+(asset?.in_library?'移出资产库':'保存至资产库');}});
    document.querySelectorAll('[data-history-retry]').forEach(button=>button.onclick=async()=>{button.disabled=true;await assetLibrary.retryFile(findAsset(button.dataset.historyRetry));if(current())showCanvasHistoryPanel();});
    document.querySelectorAll('#canvasPanelBody [data-retry-task]').forEach(button => button.onclick = () => retryTask(tasks.find(task => task.id === button.dataset.retryTask)));
    document.querySelectorAll('[data-history-add]').forEach(button => button.onclick = async () => {
      if (state.project?.id !== projectId) return;
      button.disabled = true;
      try {
        const asset = findAsset(button.dataset.historyAdd) || await api(`/api/tasks/${encodeURIComponent(button.dataset.historyAdd)}/history-asset`, json({}));
        if (state.project?.id === projectId) { addLibraryAsset(asset); toast('已添加到画布'); }
      } catch (error) { toast(error.message); }
      finally { if (button.isConnected) button.disabled = false; }
    });
  } catch (error) {
    if (current()) $('#canvasPanelBody').innerHTML = `<div class="panel-empty"><b>读取失败</b><small>${escapeHtml(error.message)}</small></div>`;
  }
}
function showCanvasHelp() {
  openCanvasPanel('画布操作', '移动和抓手可从底部工具菜单切换', '<div class="help-list"><div><kbd>V</kbd><span>移动：选择、拖动节点</span></div><div><kbd>H</kbd><span>抓手：平移整张画布</span></div><div><kbd>Space</kbd><span>按住临时抓手，松开恢复原工具</span></div><div><kbd>滚轮</kbd><span>以鼠标位置为中心缩放</span></div><div><kbd>端口</kbd><span>先点输出，再点输入完成连接</span></div><div><kbd>框选</kbd><span>拖动空白选择多个节点，Shift 点击可增减选择</span></div><div><kbd>Ctrl+G</kbd><span>选中节点打组（也支持 Alt+G）</span></div><div><kbd>Ctrl+Shift+G</kbd><span>解组，保留节点和连线</span></div><div><kbd>Ctrl+Z</kbd><span>按操作顺序撤销；Ctrl+Shift+Z 或 Ctrl+Y 重做</span></div></div>');
}
const toolIcons = {
  move: icon('move'),
  hand: icon('hand')
};
function closeToolMenu(focus = false) {
  setSurfaceVisible($('#toolMenu'), false);
  $('#dockToolTrigger').setAttribute('aria-expanded', 'false');
  if (focus) $('#dockToolTrigger').focus();
}
function setCanvasTool(tool, notify = true) {
  canvasConnections?.reset();
  state.canvasTool = tool === 'hand' ? 'hand' : 'move';
  state.connectMode = false;
  state.pendingPort = null;
  const hand = state.canvasTool === 'hand';
  $('#canvas').classList.remove('connect-mode');
  $('#dockConnect').classList.remove('dock-active');
  $('#canvas').classList.toggle('hand-mode', hand);
  $('#canvas').classList.toggle('move-mode', !hand);
  $('#dockSelect').setAttribute('aria-checked', String(!hand));
  $('#dockHand').setAttribute('aria-checked', String(hand));
  $('#activeToolIcon').innerHTML = toolIcons[state.canvasTool];
  $('#dockToolTrigger').classList.add('dock-active');
  $('#dockToolTrigger .dock-tooltip').textContent = hand ? '抓手工具 · H' : '移动 · V';
  $('#dockToolTrigger').setAttribute('aria-label', '画布工具：' + (hand ? '抓手' : '移动'));
  updatePortHighlights();
  closeToolMenu();
  if (notify) toast(hand ? '抓手工具：拖动画布平移' : '移动工具：选择或拖动节点');
}
function bindCanvasDock() {
  $('#dockAddNode').setAttribute('aria-haspopup', 'menu');
  $('#dockAddNode').setAttribute('aria-expanded', 'false');
  $('#dockAddNode').setAttribute('aria-controls', 'canvasPanel');
  $('#dockAddNode').onclick = () => {
    if (surfaceIsOpen($('#canvasPanel')) && $('#canvasPanel').dataset.kind === 'palette' && $('#canvasPanel').dataset.anchor === 'dock') closeCanvasPanel();
    else { closeToolMenu(); showNodePalette(); }
  };
  $('#dockMaterial').onclick = () => showView('assets');

  $('#dockHistory').onclick = showCanvasHistoryPanel;
  $('#dockHelp').onclick = showCanvasHelp;
  $('#dockShortcuts').onclick = showCanvasHelp;
  $('#canvasPanelClose').onclick = closeCanvasPanel;
  // Dismiss bubbles on activation/Escape so they never cover an opened panel.
  document.querySelectorAll('.canvas-dock button:has(.dock-tooltip)').forEach(button => {
    button.addEventListener('pointerenter', () => button.classList.remove('tooltip-dismissed'));
    button.addEventListener('focus', () => button.classList.remove('tooltip-dismissed'));
    button.addEventListener('pointerdown', () => button.classList.add('tooltip-dismissed'));
    button.addEventListener('click', () => button.classList.add('tooltip-dismissed'));
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape') document.querySelectorAll('.canvas-dock button').forEach(button => button.classList.add('tooltip-dismissed'));
  });
  document.querySelectorAll('[data-tool-icon]').forEach(el => { el.innerHTML = toolIcons[el.dataset.toolIcon]; });
  $('#dockToolTrigger').onclick = () => {
    const open = !surfaceIsOpen($('#toolMenu'));
    if (open) closeCanvasPanel();
    setSurfaceVisible($('#toolMenu'), open);
    $('#dockToolTrigger').setAttribute('aria-expanded', String(open));
    if (open) (state.canvasTool === 'hand' ? $('#dockHand') : $('#dockSelect')).focus();
  };
  $('#toolPicker').onkeydown = event => {
    if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
      event.preventDefault();
      setSurfaceVisible($('#toolMenu'), true);
      $('#dockToolTrigger').setAttribute('aria-expanded', 'true');
      (document.activeElement === $('#dockSelect') ? $('#dockHand') : $('#dockSelect')).focus();
    }
  };
  $('#dockSelect').onclick = () => { setCanvasTool('move', false); $('#dockToolTrigger').focus(); };
  $('#dockHand').onclick = () => { setCanvasTool('hand', false); $('#dockToolTrigger').focus(); };
  document.addEventListener('pointerdown', event => { if (!event.target.closest('#toolPicker')) closeToolMenu(); });
  $('#dockConnect').onclick = () => {
    const enabled = !state.connectMode;
    setCanvasTool('move', false);
    state.connectMode = enabled;
    $('#canvas').classList.toggle('connect-mode', enabled);
    $('#dockConnect').classList.toggle('dock-active', enabled);
    $('#dockToolTrigger').classList.toggle('dock-active', !enabled);
    toast(enabled ? '连接模式：点击节点端口完成连线' : '已退出连接模式');
  };
  setCanvasTool('move', false);
}
function bindModelControls() {
  $('#modelSelect').onchange = event => {
    state.selectedModelId = event.target.value; updateModelStatus(); applyModelCapabilities(); captureSettings(); renderNodes();
  };
}

let bootInProgress = false;
let bootFinished = false;
let waitingForMarket = false;

function marketBootStatus(status) {
  if (bootFinished) return;
  if (status.state === 'connecting') {
    document.documentElement.dataset.appBoot = 'loading';
    $('#appBootMessage').textContent = '正在连接集市…';
    $('#appBootRetry').hidden = true;
  } else if (status.state === 'error') {
    waitingForMarket = true;
    document.documentElement.dataset.appBoot = 'error';
    $('#appBootMessage').textContent = status.message;
    const retry = $('#appBootRetry');
    retry.hidden = false;
    retry.onclick = () => {
      if (new URLSearchParams(location.search).get('embedded') === '1' && navigateToMarketEntry()) return;
      void initializeMarket({ authenticated: true, onStatus: marketBootStatus });
    };
  } else if (status.connected && waitingForMarket && !bootInProgress) {
    // Banner retry only resumes the untouched bootstrap; listeners are bound once.
    void boot();
  }
}

async function boot() {
  if (bootInProgress || bootFinished) return;
  bootInProgress = true;
  try {
    // Market entry first establishes its own page session, without a local login.
    const market = await initializeMarket({ authenticated: true, onStatus: marketBootStatus });
    if (isMarketSessionRequired() && !market.connected) {
      waitingForMarket = true;
      return;
    }
    waitingForMarket = false;
    state.user = await session.load();
    document.documentElement.dataset.appBoot = 'loading';
    $('#appBootMessage').textContent = '正在加载…';
    $('#appBootRetry').hidden = true;
    [state.options, state.characters, state.models, state.projects, state.assets, state.llm, state.conversations] = await Promise.all(['/api/options','/api/characters','/api/models','/api/projects','/api/assets','/api/llm','/api/conversations'].map(url => api(url)));
    $('#modelsBack').onclick = returnFromModelManagement;
    renderOptions(); bindModelControls(); bindNavigation(); bindRailToggles(); bindCanvas(); bindCanvasDock();
    canvasWorkspace = createCanvasWorkspace({ state, icon, escapeHtml, selectedNode, nodeFootprint, updateSelection,
      toggleCanvasAgent: () => canvasAgent.toggle(),
      renderNodes, fitCanvas, setZoom, scheduleSave, showView, showNodePalette, closeCanvasPanel,
      showCanvasHistoryPanel, newProjectModal, openModelManagement, setRailState, toast, addLibraryAsset,
      nodeAsset, videoPreviewUrl, upsertAsset, generateNodeImage, generateNodeAudio, openScriptWorkflow, expandScriptShots,
      chooseNodeMedia, uploadNodeMedia, generateFromImage,
      prepareVideoReference,
      saveNode: node => node.type === 'video' ? assetLibrary.saveTask(state.nodeTasks[node.id]) : assetLibrary.saveNode(node),
      refreshGroups: () => canvasGroups?.refresh(), arrangeGroupedCanvas: () => canvasGroups?.arrangeCanvas(),
      captureSettings, selectAsset, videoReferences: video => resolveVideoAssets(video,state.nodes,state.edges,state.assets,state.characters).entries.map(entry=>entry.asset),
      request: api, videoInput: node => resolveVideoInput(node, state.nodes, state.edges) });
    canvasGroups = createCanvasGroups({state,icon,escapeHtml,nodeFootprint,renderNodes,renderEdges,history:canvasHistory,
      refreshWorkspace: () => canvasWorkspace?.refresh(), save: scheduleSave, fit: fitCanvas, toast,
      selectionChanged: () => {syncNodeSelectionVisuals();loadNodeSettings();} });
    installCanvasMentions();
    $('#projectNameInput').oninput = scheduleSave;
    for (const id of ['promptInput','negativeInput']) $('#'+id).oninput = captureSettings;
    for (const id of ['ratioSelect','durationSelect','resolutionSelect']) $('#'+id).onchange = captureSettings;
    $('#saveState').onclick = () => saveProject(false).catch(() => {});
    $('#duplicateProjectBtn').onclick = () => state.project && duplicateProject(state.project.id);
    $('#deleteProjectBtn').onclick = () => state.project && confirmDeleteProject(state.project.id);
    $('#uploadBox').onclick = () => $('#fileInput').click();
    $('#chooseReferenceBtn').onclick = showAssetPanel;
    $('#fileInput').onchange = async event => { const files = [...event.target.files]; const resourceId = state.pendingScriptResourceId; state.pendingScriptResourceId = ''; if (resourceId) await uploadScriptResource(resourceId, files); else await uploadFiles(files); event.target.value = ''; };
    window.addEventListener('beforeunload', event => { if (state.dirty) { event.preventDefault(); event.returnValue = ''; } });
    window.addEventListener('keydown', event => {
      if (event.defaultPrevented || event.isComposing || event.target.isContentEditable || !$('#modalBackdrop').hidden || document.querySelector('#imageGenerationOverlay,#audioGenerationOverlay,dialog[open]') || event.target.closest?.('input,textarea,select,[contenteditable="true"]')) return;
      if ((event.ctrlKey || event.metaKey) && event.code === 'KeyS') { event.preventDefault(); saveProject(false).catch(() => {}); }
      if ((event.ctrlKey || event.metaKey) && !event.altKey && state.view === 'canvas' && ['KeyZ','KeyY'].includes(event.code)) {
        event.preventDefault();
        if (event.code === 'KeyY' || event.shiftKey) canvasHistory.redo(); else canvasHistory.undo();
        return;
      }
      if (event.key === 'Delete' && state.view === 'canvas' && state.selectedEdgeIndex !== null) {
        event.preventDefault();
        const removed = state.edges[state.selectedEdgeIndex], remaining = state.edges.filter((_, index) => index !== state.selectedEdgeIndex);
        detachScriptResourceEdges(state.nodes, removed ? [removed] : [], remaining);
        state.edges = remaining; state.selectedEdgeIndex = null;
        updatePortHighlights(); loadNodeSettings(); scheduleSave(); toast('连线已删除'); return;
      }
      if ((event.key === 'Delete' || event.key === 'Backspace') && state.view === 'canvas') {
        const ids = state.selectedNodeIds?.size ? [...state.selectedNodeIds] : (selectedNode() ? [state.selectedNodeId] : []);
        if (ids.length) { event.preventDefault(); deleteNodes(ids); }
      }
    });
    const entry = new URLSearchParams(location.search);
    const resumeId = conversationResume.target(state.user, state.conversations, entry);
    if (entry.get('embedded') === '1') document.body.classList.add('agent-embedded');
    if(entry.get('conversation'))await openConversation(entry.get('conversation'));
    else if (entry.get('project')) await selectProject(entry.get('project'));
    else if (['projects','works'].includes(entry.get('view'))) showView('projects');
    else if (entry.get('view') === 'canvas' && state.projects.length) await selectProject(state.projects[0].id);
    else if (resumeId) {
      try { await openConversation(resumeId); }
      catch (error) {
        if (error.status !== 404) throw error;
        conversationResume.forget(state.user);showView('home');loadNodeSettings();
      }
    }
    else { showView('home'); loadNodeSettings(); }
    if (entry.get('embedded') === '1') requestAnimationFrame(() => fitCanvas());
    bootFinished = true;
    document.documentElement.removeAttribute('data-app-boot');
    $('#appBootScreen')?.remove();
  } catch(error) {
    document.documentElement.dataset.appBoot = 'error';
    $('#appBootMessage').textContent = `页面加载失败：${error.message}`;
    const retry = $('#appBootRetry'); retry.hidden = false;
    retry.onclick = () => location.reload();
  } finally {
    bootInProgress = false;
  }
}

$('#renameProject').onclick = editProjectNameInline; $('#modalClose').onclick = closeModal; $('#modalBackdrop').onclick = event => { if (event.target === $('#modalBackdrop')) closeModal(); }; $('#saveProjectBtn').onclick = () => saveProject(false).catch(() => {}); $('#generateBtn').onclick = submitTask;
const agentChat = createAgentChat({ api, json, escapeHtml, toast,
  onPreviewAsset:previewGeneratedAsset,
  onSaveAsset:(asset,inLibrary)=>assetLibrary.setLibraryMembership(asset,inLibrary),
  onConversationSnapshot:run=>{if(state.view==='agent'&&$('#agentView').dataset.homeMode==='agent'){homeHub?.showConversation();state.activeConversationId=run.project_id;conversationResume.remember(state.user,run.project_id);history.replaceState(null,'',`?conversation=${encodeURIComponent(run.project_id)}`);}const stamp=`${run.id}:${run.updated_at}`;if(state.conversationStamp!==stamp){state.conversationStamp=stamp;refreshConversations().catch(error=>toast(error.message));}},
  beforeStart: () => flushSave(),
  onProjectSnapshot: async (projectId,revision) => {
    const canRefresh=()=>state.view==='canvas'&&state.project?.id===projectId&&!state.dirty&&!state.saveInFlight&&!state.drag&&!state.switchingProject;
    if(!canRefresh()||!Number.isFinite(revision)||revision<=(state.project.revision||0))return;
    const project=await api(`/api/projects/${encodeURIComponent(projectId)}`);
    if(!canRefresh()||project.revision<=(state.project.revision||0))return;
    state.project=project;state.projects=state.projects.map(item=>item.id===projectId?project:item);
    state.nodes=normaliseNodes(project.nodes);state.groups=normalizeGroups(project.groups||[],state.nodes);
    state.edges=(project.edges||[]).filter(edge=>state.nodes.some(n=>n.id===edge.from)&&state.nodes.some(n=>n.id===edge.to));
    if(!state.nodes.some(node=>node.id===state.selectedNodeId))state.selectedNodeId=state.nodes[0]?.id;
    state.selectedNodeIds=new Set([...state.selectedNodeIds].filter(id=>state.nodes.some(node=>node.id===id)));
    renderProject();renderProjects();loadNodeSettings();canvasWorkspace?.refresh();canvasHistory.reset();await loadHistory();
  },
  onModels: () => openModelManagement('text'),
});
canvasAgent=createCanvasAgent({chat:agentChat,state,icon,toast,beforeOpen:flushSave,
  refreshCanvas:()=>canvasWorkspace?.refresh(),
  contextText:()=>[`项目：${state.project?.name||'未命名'}`,...state.nodes.map(node=>`${node.title||node.type}：${node.settings?.prompt||node.text||node.script_data?.title||''}`)].join('\n').slice(0,6000)
});
homeComposer=createHomeComposer({api,json,escapeHtml,icon,toast,state,
  onSaveTask:(task,inLibrary)=>assetLibrary.saveTask(task,inLibrary),onRetryFile:asset=>assetLibrary.retryFile(asset),
  onModeChange:()=>{conversationResume.remember(state.user,state.activeConversationId);renderProjects();},
  onPreviewAsset:previewGeneratedAsset,
  onSaveAsset:(asset,inLibrary)=>assetLibrary.setLibraryMembership(asset,inLibrary),
  onModels:kind=>openModelManagement(kind),onAssets:()=>showView('assets'),
  onConversation:id=>openConversation(id).catch(error=>toast(error.message)),
  onConversations:async id=>{if(id){state.activeConversationId=id;conversationResume.remember(state.user,id);history.replaceState(null,'',`?conversation=${encodeURIComponent(id)}`);}await refreshConversations();}
});
homeHub=createHomeHub({state,composer:homeComposer,chat:agentChat,icon,escapeHtml,toast,
  onProjects:()=>showView('projects'),onOpenCanvas:id=>selectProject(id).catch(error=>toast(error.message)),
  onCreateCanvas:async ({kind,prompt})=>{
    await flushSave();let nodes=[],edges=[];
    if(kind!=='blank'){
      nodes.push({id:crypto.randomUUID(),type:'prompt',title:'创作想法',x:140,y:180,settings:{...defaultSettings(),prompt}});
      if(['image','video'].includes(kind)){const target={id:crypto.randomUUID(),type:kind==='image'?'asset':'video',title:kind==='image'?'图片素材':'视频生成',x:620,y:180,settings:{...defaultSettings(),prompt:''}};nodes.push(target);edges.push({from:nodes[0].id,to:target.id});}
    }
    const project=await api('/api/projects',json({name:prompt?prompt.slice(0,30):kind==='image'?'图像创作':kind==='video'?'视频创作':'未命名画布',nodes,edges}));state.projects.unshift(project);await selectProject(project.id);
  }
});
window.addEventListener('message', async event => {
  if (event.origin !== location.origin || event.source !== window.parent || !document.body.classList.contains('agent-embedded')) return;
  if (event.data?.type === 'jht-agent-canvas-release') {
    state.agentHandoff = true;
    try {
      await flushSave();
      event.source.postMessage({type:'jht-agent-canvas-released',requestId:event.data.requestId},event.origin);
    } catch (error) {
      state.agentHandoff = false;
      event.source.postMessage({type:'jht-agent-canvas-released',requestId:event.data.requestId,error:error.message},event.origin);
    }
    return;
  }
  if (event.data?.type !== 'jht-agent-refresh' || state.agentHandoff) return;
  try { await flushSave(); await selectProject(event.data.projectId); } catch (error) { toast(error.message); }
});
boot();

import { createHomePreferences } from './home-preferences.js?v=parameter-escape-8';
import { expandAssetMentions } from './asset-mentions.js?v=inline-chips-1';
import { createVisualStylePicker } from './visual-style-picker.js?v=4';
// Canvas-native media composers. Drafts and task IDs live on their owning node;
// a task may finish while another node is selected without changing selection.
export function createInlineNodeMedia(ctx) {
  const {state, api, json, escapeHtml:esc, icon} = ctx;
  const entries=new Map();
  const preferences=createHomePreferences({root:document.querySelector('#canvasView'),icon,escapeHtml:esc});
  const stylePicker=createVisualStylePicker({api,escapeHtml:esc,icon,onError:ctx.toast});
  let registry=null;
  const kindFor=node=>node.type==='audio'?'audio':node.type==='video-asset'?'video':'image';
  const valid=entry=>state.project?.id===entry.projectId && state.nodes.includes(entry.node);
  const busy=entry=>entry.submitting||['queued','running'].includes(entry.status);
  const settingsKey=node=>node.type==='audio'?'audio_settings':'image_settings';
  const taskKey=node=>node.type==='audio'?'audio_task_id':'image_task_id';
  function draft(entry,patch) {
    if(!valid(entry)) return;
    entry.node[settingsKey(entry.node)]={...entry.node[settingsKey(entry.node)],...patch};
    ctx.save();
  }
  function sources(node) {
    return state.edges.filter(edge=>edge.to===node.id&&edge.relation!=='agent-flow').map(edge=>state.nodes.find(item=>item.id===edge.from)).filter(Boolean);
  }
  function references(entry) {
    const ids=[...new Set([...sources(entry.node).map(node=>ctx.nodeAsset(node)?.id),...(entry.node[settingsKey(entry.node)]?.reference_asset_ids||[])].filter(Boolean))];
    return ids.map(id=>state.assets.find(asset=>asset.id===id)).filter(asset=>asset && (kindFor(entry.node)==='audio'?['audio','image','prop']:['image','prop']).includes(asset.kind||'image'));
  }
  function upstream(node) {return sources(node).filter(item=>item.type==='prompt').map(item=>item.settings?.prompt||'').filter(Boolean).join('\n');}
  async function models(reload=false) {
    if(!registry||reload) registry=api('/api/model-registry').then(data=>data.models||[]).catch(error=>{registry=null;throw error;});
    return registry;
  }
  function entryFor(node) {
    const key=state.project.id+':'+node.id;
    let entry=entries.get(key);
    if(entry && entry.node!==node) {clearTimeout(entry.timer);entries.delete(key);entry=null;}
    if(!entry) {
      entry={node,projectId:state.project.id,root:document.createElement('section'),loading:true,models:[],status:'',error:'',submitting:false,pollErrors:0,appliedId:node[settingsKey(node)]?.last_result_asset_id||''};
      entry.root.className='inline-media-composer'; entry.root.dataset.nodeComposer=node.id;
      entries.set(key,entry);
      load(entry);
    }
    return entry;
  }
  async function load(entry) {
    try {
      entry.models=(await models()).filter(model=>model.kind===kindFor(entry.node));
      if(!valid(entry)) return;
      const saved=entry.node[settingsKey(entry.node)]||{};
      entry.modelId=entry.models.find(model=>model.id===saved.model_config_id)?.id || entry.models.find(model=>model.is_default)?.id || entry.models[0]?.id || '';
      if(entry.node[taskKey(entry.node)]) await poll(entry);
    } catch(error) {entry.error=error.message;}
    finally {entry.loading=false;if(valid(entry)) render(entry);}
  }
  function library(host,{kind,onPick,title='添加素材',exclude=[]}) {
    host.querySelector('.inline-asset-picker')?.remove();
    let collection='history';
    const pane=document.createElement('section');pane.className='inline-asset-picker';
    pane.innerHTML=`<div class="inline-picker-head"><span>${esc(title)}</span><button type="button" aria-label="关闭素材列表">${icon('close')}</button></div><div class="al-collection-tabs" role="tablist" aria-label="选择内容范围"><button type="button" role="tab" data-inline-collection="history" aria-selected="true">生成历史</button><button type="button" role="tab" data-inline-collection="library" aria-selected="false">资产库</button></div><input type="search" placeholder="搜索素材名称" aria-label="搜索节点素材"><div class="inline-picker-grid"></div><p class="inline-picker-status" role="status"></p>`;
    host.append(pane);
    const status=pane.querySelector('.inline-picker-status');
    const draw=()=>{
      const query=pane.querySelector('input').value.toLowerCase();
      const assets=state.assets.filter(asset=>(collection==='history'||asset.in_library)&&(Array.isArray(kind)?kind:[kind]).includes(asset.kind||'image')&&!exclude.includes(asset.id)&&String(asset.name).toLowerCase().includes(query));
      pane.querySelector('.inline-picker-grid').innerHTML=assets.map(asset=>`<button type="button" data-inline-asset="${esc(asset.id)}" title="${esc(asset.name)}">${['image','prop'].includes(asset.kind||'image')?`<img src="${esc(asset.url)}" alt="" loading="lazy">`:icon(asset.kind==='audio'?'audio':'video')}<span>${esc(asset.name)}</span></button>`).join('');
      status.textContent=assets.length?'':'暂无匹配素材，可直接上传文件';
      pane.querySelectorAll('[data-inline-asset]').forEach(button=>button.onclick=async()=>{
        const asset=state.assets.find(item=>item.id===button.dataset.inlineAsset);
        try {await onPick(asset);pane.remove();} catch(error){status.textContent=error.message;}
      });
    };
    pane.querySelector('.inline-picker-head button').onclick=()=>pane.remove();
    pane.querySelectorAll('[data-inline-collection]').forEach(button=>button.onclick=()=>{collection=button.dataset.inlineCollection;pane.querySelectorAll('[data-inline-collection]').forEach(item=>item.setAttribute('aria-selected',String(item===button)));draw();});
    pane.querySelector('input').oninput=draw;draw();
    api('/api/assets').then(assets=>{if(Array.isArray(assets)){state.assets=assets;if(pane.isConnected)draw();}}).catch(error=>{if(pane.isConnected)status.textContent=error.message;});
    ctx.refresh();return pane;
  }
  function chooseFile({kind,onFile,host,multiple=false}) {
    const input=document.createElement('input');input.type='file';input.multiple=multiple;input.className='inline-file-input';input.hidden=true;
    input.accept=kind==='audio'?'.wav,.mp3,.m4a,.ogg':kind==='video'?'.mp4,.webm':kind==='reference-audio'?'.wav,.mp3,.ogg,.jpg,.jpeg,.png,.webp':'.jpg,.jpeg,.png,.webp';
    host.querySelector('.inline-file-input')?.remove();host.append(input);
    input.onchange=async()=>{try {for(const file of input.files) await onFile(file);}catch(error){ctx.toast(error.message);}finally{input.remove();}};
    input.click();
  }
  async function upload(file,entry,reference=false) {
    entry.error='';entry.uploading=true;render(entry);
    try {
      const form=new FormData();form.append('file',file);form.append('project_id',entry.projectId);
      if(!reference&&entry.node.type==='prop')form.append('kind','prop');
      const asset=await api('/api/assets',{method:'POST',body:form});ctx.upsertAsset(asset);
      if(valid(entry)) {if(reference)addReference(entry,asset);else ctx.bindAsset(entry.node,asset);}
    }catch(error){entry.error=error.message;}
    finally{entry.uploading=false;if(valid(entry))render(entry);}
  }
  function addReference(entry,asset) {
    const refs=references(entry),audio=kindFor(entry.node)==='audio';
    if(refs.some(item=>item.id===asset.id))return;
    if(refs.length>=(audio?3:10))throw new Error(audio?'最多添加 3 条参考音频':'最多添加 10 张参考图片');
    if(audio && ((asset.kind!=='audio'&&refs.length)||(refs.some(item=>item.kind!=='audio'))))throw new Error('参考图片不能与音频或其他参考图片混用');
    if(audio&&asset.kind!=='audio'&&entry.node.audio_settings?.speaker)throw new Error('参考图片不能与音色 ID 混用');
    if(asset.size_bytes>10*1024*1024)throw new Error('单个参考素材不能超过 10 MB');
    draft(entry,{reference_asset_ids:[...(entry.node[settingsKey(entry.node)]?.reference_asset_ids||[]),asset.id]});render(entry);
  }
  function render(entry) {
    if(!valid(entry))return;
    preferences.close();stylePicker.close();
    const node=entry.node,audio=kindFor(node)==='audio',saved=node[settingsKey(node)]||{},linked=upstream(node),refs=references(entry),locked=busy(entry);
    const model=entry.models.find(item=>item.id===entry.modelId);
    const mode=saved.mode||'sfx';
    entry.root.innerHTML=`<div class="inline-composer-tools"><button type="button" data-inline-action="reference">${icon('plus')}参考</button><button type="button" data-inline-action="upload-reference">${icon('upload')}上传参考</button>${audio?`<button type="button" data-inline-action="replace">${icon('library')}资产库</button><button type="button" data-inline-action="upload">${icon('upload')}上传音频</button><button type="button" data-inline-action="models">${icon('settings')}管理模型</button>`:''}</div>
      ${audio?`<div class="inline-audio-modes">${[['sfx','音效'],['voice','配音'],['music','音乐']].map(([value,label])=>`<button type="button" data-inline-mode="${value}" aria-pressed="${mode===value}" ${locked?'disabled':''}>${label}</button>`).join('')}</div>`:''}
      <div class="inline-reference-list">${refs.map((asset,index)=>`<div title="${esc(asset.name)}" ${asset.kind==='audio'?`data-audio-preview="${esc(asset.url)}"`:''}>${asset.kind==='audio'?'<span class="reference-audio-icon">♪</span>':`<img src="${esc(asset.url)}" alt="${esc(asset.name)}">`}<span class="reference-number">${index+1}</span>${asset.kind==='audio'&&asset.duration?`<small class="reference-duration">${Number(asset.duration).toFixed(1)}s</small>`:''}<button type="button" data-inline-remove="${esc(asset.id)}" aria-label="移除参考 ${esc(asset.name)}" ${locked?'disabled':''}>${icon('close')}</button></div>`).join('')}</div>
      <textarea class="inline-generation-prompt" aria-label="${audio?'音频':'图片'}生成描述" maxlength="${audio?3000:4000}" placeholder="${audio?'描述声音、配音内容或音乐氛围…':'描述画面内容、构图、光线与风格…'}" ${locked?'disabled':''} ${linked?'readonly':''}>${esc(linked||saved.prompt||'')}</textarea>
      ${audio?`<details class="inline-generation-advanced"><summary>高级设置</summary><label>素材名称<input data-inline-field="name" maxlength="160" value="${esc(saved.name||node.assetName||node.title||'')}" ${locked?'disabled':''}></label>${audio?`<label>音色 ID<input data-inline-field="speaker" maxlength="200" value="${esc(saved.speaker||'')}" placeholder="可选，与参考图片互斥" ${locked?'disabled':''}></label><label>输出格式<select data-inline-field="format" ${locked?'disabled':''}><option value="wav" ${saved.audio_config?.format!=='mp3'&&saved.audio_config?.format!=='ogg_opus'?'selected':''}>WAV</option><option value="mp3" ${saved.audio_config?.format==='mp3'?'selected':''}>MP3</option><option value="ogg_opus" ${saved.audio_config?.format==='ogg_opus'?'selected':''}>OGG Opus</option></select></label><label>语速<input type="number" data-inline-field="speech_rate" min="-50" max="100" value="${Number(saved.audio_config?.speech_rate)||0}" ${locked?'disabled':''}></label><label>音量<input type="number" data-inline-field="loudness_rate" min="-50" max="100" value="${Number(saved.audio_config?.loudness_rate)||0}" ${locked?'disabled':''}></label>`:''}<label class="inline-checkbox"><input type="checkbox" data-inline-field="watermark" ${saved.watermark===true?'checked':''} ${locked?'disabled':''}>添加水印</label></details>`:''}
      <div class="inline-generation-status" role="status" aria-live="polite">${esc(entry.error|| (entry.uploading?'正在上传…':entry.loading?'正在加载模型…':locked?entry.status==='queued'?'任务排队中…':'正在生成…':entry.status==='succeeded'?'已生成并更新当前节点':!model?.api_key_set?'请先在模型管理中配置对应模型':'') )}${entry.pollErrors>=3?'<button type="button" class="inline-retry-status">重新查询进度</button>':''}</div>
      <div class="inline-composer-bottom"><select class="inline-model-select" aria-label="${audio?'音频':'图片'}生成模型" ${entry.loading||locked?'disabled':''}>${entry.models.length?entry.models.map(item=>`<option value="${esc(item.id)}" ${entry.modelId===item.id?'selected':''}>${esc(item.name||item.model_id)}${item.is_default?' · 默认':''}</option>`).join(''):'<option value="">暂无可用模型</option>'}</select>${audio?`<span class="inline-output-label">${String(saved.audio_config?.format||'wav').toUpperCase()}</span>`:'<div class="inline-image-parameters"></div>'}<button class="inline-generate-button" type="button" ${entry.loading||locked||entry.uploading||!model?.api_key_set?'disabled':''}>${icon('play')}${locked?'生成中…':audio?'生成音频':'生成图片'}</button></div>`;
    const find=selector=>entry.root.querySelector(selector);
    find('.inline-generation-prompt').oninput=event=>draft(entry,{prompt:event.target.value});
    find('.inline-model-select').onchange=event=>{entry.modelId=event.target.value;draft(entry,{model_config_id:entry.modelId});render(entry);};
    if(!audio){
      stylePicker.button(find('.inline-composer-tools'),{getValue:()=>entry.node.image_settings?.visual_style,onChange:value=>draft(entry,{visual_style:value}),disabled:locked||entry.loading});
      const bottom=find('.inline-composer-bottom'),modelSelect=find('.inline-model-select');
      preferences.button(bottom,{label:()=>modelSelect.selectedOptions[0]?.textContent||'暂无模型',groups:[{select:modelSelect,title:'选择模型'}],glyph:'sparkle'}).classList.add('inline-image-model-trigger');
      bottom.insertBefore(bottom.lastElementChild,find('.inline-image-parameters'));
      const container=find('.inline-image-parameters');
      const ratios=['adaptive','1:1','3:4','16:9','4:3','9:16','2:3','3:2','21:9'];
      const sizes=model?.capabilities?.resolutions?.length?model.capabilities.resolutions:['1K','2K','4K'];
      const ratioValue=ratios.includes(saved.aspect_ratio)?saved.aspect_ratio:'adaptive';
      const sizeValue=sizes.includes(saved.size)?saved.size:sizes.includes('2K')?'2K':sizes[0];
      container.innerHTML=`<select aria-label="图片比例">${ratios.map(value=>`<option value="${value}" ${value===ratioValue?'selected':''}>${value==='adaptive'?'智能':value}</option>`).join('')}</select><select aria-label="图片分辨率">${sizes.map(value=>`<option ${value===sizeValue?'selected':''}>${esc(value)}</option>`).join('')}</select><select aria-label="图片数量">${[1,2,3,4].map(value=>`<option value="${value}" ${value===(saved.count||1)?'selected':''}>${value} 张</option>`).join('')}</select>`;
      const [ratio,size,count]=container.querySelectorAll('select');
      for(const select of [ratio,size,count])select.onchange=()=>draft(entry,{aspect_ratio:ratio.value,size:size.value,count:Number(count.value)});
      const button=preferences.button(container,{label:()=>`${ratio.value==='adaptive'?'智能比例':ratio.value} · ${size.value} · ${count.value} 张`,groups:[{select:ratio,title:'选择比例',ratios:true},{select:size,title:'选择分辨率'},{select:count,title:'选择生成数量'}],glyph:'imageNode'});
      button.disabled=locked||entry.loading;
      bottom.querySelector('.inline-image-model-trigger').disabled=locked||entry.loading;
    }

    entry.root.querySelectorAll('[data-inline-field]').forEach(field=>field.onchange=()=>{
      const key=field.dataset.inlineField,value=field.type==='checkbox'?field.checked:field.type==='number'?Number(field.value):field.value;
      if(['format','speech_rate','loudness_rate'].includes(key))draft(entry,{audio_config:{...saved.audio_config,[key]:value,...(key==='format'?{sample_rate:value==='ogg_opus'?48000:value==='mp3'?44100:40000}:{})}});
      else draft(entry,{[key]:value});
    });
    entry.root.querySelectorAll('[data-inline-mode]').forEach(button=>button.onclick=()=>{draft(entry,{mode:button.dataset.inlineMode});render(entry);});
    entry.root.querySelectorAll('[data-inline-remove]').forEach(button=>button.onclick=()=>{
      const id=button.dataset.inlineRemove;
      const graph=sources(node).find(item=>ctx.nodeAsset(item)?.id===id);
      if(graph)ctx.removeReferenceEdge(graph.id,node.id);
      draft(entry,{reference_asset_ids:(saved.reference_asset_ids||[]).filter(value=>value!==id)});render(entry);
    });
    entry.root.querySelectorAll('[data-inline-action]').forEach(button=>button.onclick=async()=>{
      const action=button.dataset.inlineAction;
      if(action==='reference')library(entry.root,{kind:audio?['audio','image','prop']:['image','prop'],exclude:refs.map(item=>item.id),title:'添加参考素材',onPick:asset=>addReference(entry,asset)});
      if(action==='replace')pick(node);
      if(action==='upload'||action==='upload-reference')chooseFile({host:entry.root,kind:action==='upload-reference'?(audio?'reference-audio':'image'):audio?'audio':'image',onFile:file=>upload(file,entry,action==='upload-reference')});
      if(action==='models')ctx.manageModels(audio?'audio':'image',{onReturn:async()=>{registry=null;entry.loading=true;render(entry);await load(entry);}});
    });
    find('.inline-generate-button').onclick=()=>generate(entry);
    if(find('.inline-retry-status'))find('.inline-retry-status').onclick=()=>{entry.pollErrors=0;poll(entry);};
    if(locked)entry.root.querySelectorAll('[data-inline-action="reference"],[data-inline-action="upload-reference"],[data-inline-action="replace"],[data-inline-action="upload"]').forEach(button=>button.disabled=true);
    ctx.refresh();
  }
  async function accept(entry,task) {
    if(!valid(entry))return;
    entry.status=task.status;entry.error='';
    if(['succeeded','done'].includes(task.status)) {
      if(!task.asset)throw new Error('任务已完成但未返回素材，请在生成历史查看');
      if(entry.appliedId!==task.asset.id) {
        entry.appliedId=task.asset.id;
        draft(entry,{last_result_asset_id:task.asset.id});
        ctx.upsertAsset(task.asset);ctx.bindAsset(entry.node,task.asset);await ctx.flushSave();
      }
      entry.status='succeeded';
    }else if(['failed','blocked'].includes(task.status))entry.error=typeof task.error==='string'?task.error:task.error?.message||'生成未完成，请调整描述后重试';
    render(entry);
  }
  async function poll(entry) {
    clearTimeout(entry.timer);
    if(!valid(entry))return;
    const id=entry.node[taskKey(entry.node)];if(!id)return;
    try {
      const task=await api(`/api/${kindFor(entry.node)==='audio'?'audio':'images'}/generations/${encodeURIComponent(id)}`);
      entry.pollErrors=0;await accept(entry,task);
      if(valid(entry)&&['queued','running'].includes(entry.status))entry.timer=setTimeout(()=>poll(entry),1800);
    }catch(error){entry.error='读取进度失败：'+error.message;entry.pollErrors++;if(valid(entry)&&entry.pollErrors<3)entry.timer=setTimeout(()=>poll(entry),2500);render(entry);}
  }
  function imageOutput(entry) {
    const saved=entry.node.image_settings||{};
    const supported=entry.models.find(item=>item.id===entry.modelId)?.capabilities?.resolutions;
    const sizes=supported?.length?supported:['1K','2K','4K'];
    return {size:sizes.includes(saved.size)?saved.size:sizes.includes('2K')?'2K':sizes[0],aspect_ratio:saved.aspect_ratio||'adaptive',count:[1,2,3,4].includes(saved.count)?saved.count:1,visual_style:saved.visual_style||null};
  }
  async function generate(entry) {
    if(!valid(entry)||busy(entry))return;
    const node=entry.node,audio=kindFor(node)==='audio',saved=node[settingsKey(node)]||{},prompt=expandAssetMentions(upstream(node)||saved.prompt,saved.mentions,state.nodes,state.assets).trim(),refs=references(entry);
    if(!prompt){entry.error='请先填写生成描述';render(entry);return;}
    if(audio&&(refs.length>3||(refs.some(asset=>asset.kind!=='audio')&&(refs.length>1||saved.speaker)))){entry.error='参考图片不能与其他参考素材或音色 ID 混用';render(entry);return;}
    const payload={project_id:entry.projectId,name:saved.name||node.title,prompt,model_config_id:entry.modelId,watermark:!!saved.watermark,reference_asset_ids:refs.map(asset=>asset.id),...(audio?{mode:saved.mode||'sfx',speaker:saved.speaker||'',audio_config:{format:'wav',sample_rate:40000,speech_rate:0,loudness_rate:0,pitch_rate:0,enable_subtitle:false,...saved.audio_config}}:{asset_kind:node.type==='prop'?'prop':'image',...imageOutput(entry)})};
    const fingerprint=JSON.stringify(payload);
    if(!entry.uncertain||entry.fingerprint!==fingerprint)entry.requestId=crypto.randomUUID();
    entry.fingerprint=fingerprint;entry.uncertain=true;entry.submitting=true;entry.error='';render(entry);
    try {
      await ctx.flushSave();
      const task=await api(`/api/${audio?'audio':'images'}/generations`,json({...payload,request_id:entry.requestId}));
      if(!valid(entry))return;
      if(!task.id)throw new Error('任务未返回编号，请在生成历史查看');
      entry.uncertain=false;node[taskKey(node)]=task.id;ctx.save();entry.submitting=false;
      await accept(entry,task);
      if(['queued','running'].includes(entry.status))entry.timer=setTimeout(()=>poll(entry),1800);
    }catch(error){entry.error=error.message;entry.status='failed';}
    finally{entry.submitting=false;render(entry);}
  }
  function mount(node,host) {
    for(const [key,entry] of entries)if(!valid(entry)){clearTimeout(entry.timer);entries.delete(key);}
    const entry=entryFor(node);host.append(entry.root);render(entry);return entry;
  }
  function focus(node,mode='') {
    if(ctx.selectedNode()?.id!==node.id)ctx.select(node.id);
    ctx.showEditor();
    const entry=entryFor(node);
    if(mode)draft(entry,{mode});
    render(entry);entry.root.querySelector('textarea')?.focus({preventScroll:true});
  }
  function pick(node,reference=false) {
    if(ctx.selectedNode()?.id!==node.id)ctx.select(node.id);
    ctx.showEditor();
    const host=document.querySelector('#nodeDetailView:not([hidden])')||document.querySelector('#nodeEditorFields');
    return library(host,{kind:reference?['image','prop','audio','video']:kindFor(node)==='image'?['image','prop']:kindFor(node),title:reference?'添加视频参考':'选择节点素材',onPick:asset=>reference?ctx.selectVideoAsset(asset.id,node):ctx.bindAsset(node,asset)});
  }
  function mention(node,asset){
    const entry=entryFor(node);const caret=entry.root.querySelector('textarea')?.selectionStart;
    if(asset) addReference(entry,asset);else render(entry);
    const input=entry.root.querySelector('textarea');input?.focus({preventScroll:true});if(caret!=null)input?.setSelectionRange(caret,caret);
  }
  return {mount,focus,pick,library,mention};
}

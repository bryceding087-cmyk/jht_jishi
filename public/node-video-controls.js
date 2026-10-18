import { createHomePreferences } from './home-preferences.js?v=parameter-escape-8';

export function createNodeVideoControls(api, inspector, refresh) {
  const {state,icon,escapeHtml:esc}=api;
  const preferences=createHomePreferences({root:document.querySelector('#canvasView'),icon,escapeHtml:esc});
  const model=document.querySelector('#modelSelect'),parameters=inspector.querySelector('.composer-parameters');
  const modelButton=preferences.button(parameters,{label:()=>model.selectedOptions[0]?.textContent||'暂无模型',groups:[{select:model,title:'选择模型'}],glyph:'sparkle'});
  model.after(modelButton); modelButton.classList.add('composer-option');
  const mode=document.createElement('select');mode.id='nodeVideoMode';mode.innerHTML='<option value="all">全能参考</option><option value="first-last">首尾帧</option>';parameters.append(mode);
  const modeButton=preferences.button(parameters,{label:()=>mode.selectedOptions[0].textContent,groups:[{select:mode,title:'视频生成方式',menu:true}],glyph:'video'});
  modeButton.classList.add('composer-option');modelButton.after(mode,modeButton);
  const ratio=document.querySelector('#ratioSelect'),resolution=document.querySelector('#resolutionSelect');
  const outputLabel=()=>`${ratio.value} · ${resolution.value.toUpperCase()}`;
  const outputButton=preferences.button(parameters,{label:outputLabel,groups:[{select:ratio,title:'选择比例',ratios:true},{select:resolution,title:'选择分辨率'}],glyph:'imageNode'});
  outputButton.classList.add('composer-option');modeButton.after(outputButton);
  const frames=document.createElement('div');frames.className='node-video-frames home-frame-slots';frames.hidden=true;
  document.querySelector('#selectedAssets').before(frames);
  const upload=document.createElement('input');upload.type='file';upload.accept='image/*';upload.hidden=true;inspector.append(upload);
  let nodeId='',signature='',target=0;
  const node=()=>api.selectedNode();
  function change(){api.captureSettings();api.scheduleSave();signature='';refresh();}
  mode.onchange=()=>{
    const video=node();if(video?.type!=='video')return;
    video.settings.video_mode=mode.value;
    if(mode.value==='first-last'&&!video.settings.frame_asset_ids){const ids=api.videoReferences(video).filter(a=>['image','prop'].includes(a.kind)).map(a=>a.id);video.settings.frame_asset_ids=[ids[0]||null,ids[1]||null];}
    preferences.close();change();
  };
  frames.onclick=event=>{
    const video=node();if(video?.type!=='video')return;
    const pick=event.target.closest('[data-node-frame]'),remove=event.target.closest('[data-node-frame-remove]');
    if(pick){target=Number(pick.dataset.nodeFrame);upload.click();}
    if(remove){video.settings.frame_asset_ids[Number(remove.dataset.nodeFrameRemove)]=null;change();}
    if(event.target.closest('[data-node-frame-swap]')){
      video.settings.frame_asset_ids.reverse();change();
      if(!matchMedia('(prefers-reduced-motion: reduce)').matches)frames.animate([{opacity:.5,transform:'translateY(2px)'},{opacity:1,transform:'none'}],{duration:280,easing:'ease-out'});
    }
  };
  upload.onchange=async()=>{
    const file=upload.files[0],video=node(),index=target;if(!file||video?.type!=='video')return;
    try{if(!file.type.startsWith('image/'))throw new Error('首尾帧仅支持图片');const data=new FormData();data.append('file',file);const asset=await api.request('/api/assets',{method:'POST',body:data});state.assets.push(asset);video.settings.frame_asset_ids ||= [null,null];video.settings.frame_asset_ids[index]=asset.id;api.selectAsset(asset.id,video);change();}
    catch(error){api.toast(error.message);}finally{upload.value='';}
  };
  return {sync(video,visible){
    if(nodeId!==video?.id||!visible){preferences.close();nodeId=video?.id;}
    const active=visible&&video?.type==='video';frames.hidden=!active||video.settings?.video_mode!=='first-last';
    inspector.classList.toggle('node-first-last',!frames.hidden);
    if(!active)return;
    mode.value=video.settings?.video_mode==='first-last'?'first-last':'all';modeButton.querySelector('span').textContent=mode.selectedOptions[0].textContent;
    modelButton.querySelector('span').textContent=model.selectedOptions[0]?.textContent||'暂无模型';
    outputButton.querySelector('span').textContent=outputLabel();
    const ids=video.settings?.frame_asset_ids||[null,null],key=JSON.stringify(ids);
    if(signature===key)return;signature=key;
    frames.innerHTML=ids.map((id,i)=>{const asset=state.assets.find(a=>a.id===id);return `<span class="home-frame-slot"><button type="button" class="home-frame-pick" data-node-frame="${i}" aria-label="${asset?'替换':'上传'}${i?'尾帧':'首帧'}">${asset?`<img src="${esc(asset.url)}" alt="${esc(asset.name)}">`:icon('plus')}<span>${i?'尾帧':'首帧'}</span></button>${asset?`<button type="button" class="home-frame-remove" data-node-frame-remove="${i}">×</button>`:''}</span>${i===0?`<button type="button" class="home-frame-swap" data-node-frame-swap aria-label="交换首尾帧">⇄</button>`:''}`;}).join('');
  }};
}

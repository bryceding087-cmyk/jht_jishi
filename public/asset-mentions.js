import { videoReferenceCover } from './reference-video.js?v=1';
import { createMentionEditor } from './mention-editor.js?v=conversation-separation-1';
// A shared, non-modal @ picker for Agent messages and canvas prompt editors.
export function expandAssetMentions(text,mentions=[],nodes=[],assets=[]) {
  let result=String(text||'');
  for(const mention of mentions) {
    if(!mention.token||!result.includes(mention.token))continue;
    const node=nodes.find(item=>item.id===mention.node_id),asset=assets.find(item=>item.id===mention.asset_id);
    const content=node?.type==='prompt'?node.settings?.prompt:['text','script'].includes(asset?.kind)?asset.content||asset.description:mention.content||'';
    if(content)result=result.split(mention.token).join(`${mention.token}（${String(content).slice(0,2000)}）`);
  }
  return result;
}
export function attachAssetMentions({root=document,selector,getItems,getReferences=()=>[],getLinkedReferences=getReferences,onSelect,icon,escapeHtml:esc,onError=()=>{}}) {
  const menu=document.createElement('section');menu.className='asset-mention-menu';menu.hidden=true;
  menu.setAttribute('aria-label','引用素材');document.body.append(menu);
  const categories=[['image','图片','imageNode'],['video','视频','play'],['audio','音频','audio'],['text','文本','text'],['asset','资产','folder']];
  let session=null,version=0,items=[],shown=[],active=0,category='all',inserting=false,rootLinkedCount=0;
  const key=item=>item.nodeId||item.assetId||item.id;
  const editors=new WeakMap(),chosen=new WeakMap();
  const durations=new Map();
  function previewDuration(card){
    if(card.dataset.durationLoaded)return;card.dataset.durationLoaded='true';
    const url=card.dataset.audioPreview;if(!url||card.querySelector('.reference-duration'))return;
    if(!durations.has(url))durations.set(url,new Promise(resolve=>{
      const audio=new Audio();audio.preload='metadata';let timer;
      const finish=value=>{clearTimeout(timer);audio.onloadedmetadata=audio.onerror=null;audio.removeAttribute('src');audio.load();resolve(value);};
      audio.onloadedmetadata=()=>finish(Number.isFinite(audio.duration)?audio.duration:0);audio.onerror=()=>finish(0);
      timer=setTimeout(()=>finish(0),10000);audio.src=url;
    }));
    durations.get(url).then(duration=>{if(!duration||!card.isConnected||card.querySelector('.reference-duration'))return;const label=document.createElement('small');label.className='reference-duration';label.textContent=duration.toFixed(1)+'s';card.append(label);});
  }
  const kindLabel=kind=>({audio:'音频',video:'视频',text:'文本',script:'文本',character:'图片',prop:'图片'})[kind]||'图片';
  function references(input){
    const refs=getReferences(input)||[],local=chosen.get(input)||[];
    return [...refs,...local.filter(item=>!refs.some(ref=>ref.token===item.token))].map((item,index)=>({...item,number:item.number||refs.find(ref=>item.assetId&&ref.assetId===item.assetId)?.number||index+1}));
  }
  const thumbnail=item=>item.kind==='video'&&item.url?videoReferenceCover(item,esc):['image','character','prop'].includes(item.kind)&&item.url?`<img src="${esc(item.url)}" alt="">`:`<span class="mention-type-icon">${item.kind==='audio'?'♪':icon(item.kind==='video'?'play':'text')}</span>`;
  const menuThumbnail=item=>item.kind==='audio'?`<span class="mention-type-icon">${icon('audio')}</span>`:thumbnail(item);
  function decorate(input){
    if(editors.has(input))return;
    const adapter=createMentionEditor(input,{references:()=>references(input),visual:item=>`${thumbnail(item)}<span>${kindLabel(item.kind)} ${item.number}</span>`,thumbnail:item=>`${thumbnail(item)}<span class="reference-number">${item.number}</span>${item.kind==='audio'&&item.duration?`<span class="reference-duration">${Number(item.duration).toFixed(1)}s</span>`:''}`,onHover:openHover,onLeave:()=>{clearTimeout(hoverTimer);if(session?.hover)hoverTimer=setTimeout(close,300);},onChange:update,onKey:event=>{if(!event.defaultPrevented)input.onkeydown?.(event);}});
    editors.set(input,adapter);
  }
  function scan(){root.querySelectorAll(selector).forEach(decorate);root.querySelectorAll('[data-audio-preview]').forEach(previewDuration);}
  new MutationObserver(scan).observe(root,{childList:true,subtree:true});scan();
  let hoverTimer;
  async function openHover(input,anchor,item){
    clearTimeout(hoverTimer);hoverTimer=setTimeout(async()=>{
      close();category='all';active=0;
      const start=anchor.isConnected?editors.get(input).offset(anchor):-1;
      if(start<0||!anchor.isConnected)return;
      session={input,start,end:start+item.token.length,term:'',anchor,hover:true};const request=++version;
      try{const result=await getItems(input);if(request!==version)return;if(!anchor.isConnected){close();return;}items=[...new Map(result.filter(Boolean).map(item=>[key(item),item])).values()];menu.hidden=false;draw();}catch(error){onError(error.message);}
    },180);
  }
  function close(){version++;clearTimeout(categoryTimer);if(session){session.input.setAttribute('aria-expanded','false');session.input.removeAttribute('aria-activedescendant');}session=null;menu.hidden=true;}
  function query(input){
    if(input.readOnly||input.disabled)return null;
    const end=input.selectionStart??input.value.length,before=input.value.slice(0,end),match=before.match(/@([^@\n]{0,60})$/u);
    if(!match||/\s/.test(match[1]))return null;
    return {start:before.lastIndexOf('@'),end,term:match[1]};
  }
  function position(){
    if(!session)return;
    const input=session.input,adapter=editors.get(input);
    session.caretRect=adapter?.caretRect()||session.caretRect;
    const r=session.anchor?.getBoundingClientRect()||session.caretRect||(adapter?.editor||input).getBoundingClientRect();
    const width=Math.min(250,innerWidth-24);menu.style.width=width+'px';
    menu.style.setProperty('--mention-max-height',Math.min(500,innerHeight-32)+'px');
    menu.style.left=Math.max(12,Math.min(innerWidth-width-12,r.left))+'px';
    const height=menu.offsetHeight;
    menu.style.top=Math.max(12,Math.min(innerHeight-height-12,session.hover?r.bottom+8:r.top-height-8>=12?r.top-height-8:r.bottom+8))+'px';
    const flyout=menu.querySelector('.mention-flyout');
    if(flyout){
      const bounds=menu.getBoundingClientRect(),button=menu.querySelector(`[data-mention-kind="${category}"]`).getBoundingClientRect();
      const right=bounds.right+6,width=Math.min(250,innerWidth-24);
      flyout.style.width=width+'px';
      flyout.style.left=Math.max(12,Math.min(innerWidth-width-12,right+width<=innerWidth-12?right:bounds.left-width-6))+'px';
      flyout.style.top=Math.max(12,Math.min(innerHeight-flyout.offsetHeight-12,button.top-8))+'px';
    }
  }
  let categoryTimer;
  function draw(){
    if(!session)return;
    const focused=menu.querySelector('input')===document.activeElement,scroll=menu.querySelector('.mention-menu-scroll')?.scrollTop||0;
    const term=session.term.toLocaleLowerCase();
    const linked=[...new Map((getLinkedReferences(session.input)||[]).map(ref=>{
      const item=items.find(item=>key(item)===key(ref)||item.assetId&&item.assetId===ref.assetId);
      return item?[item.assetId||key(item),item]:null;
    }).filter(Boolean)).values()];
    const kind=item=>({character:'image',prop:'image',script:'text'})[item.kind]||item.kind;
    shown=term?items.filter(item=>(item.name||'').toLocaleLowerCase().includes(term)):category==='all'?linked:items.filter(item=>category==='asset'?!!item.assetId:kind(item)===category);
    rootLinkedCount=linked.length;
    const rootView=!term&&category==='all';
    active=Math.min(active,Math.max(0,(rootView?linked.length+categories.length:shown.length)-1));
    const row=(item,index)=>`<button type="button" role="option" id="mention-option-${version}-${index}" aria-selected="${index===active}" data-mention-index="${index}">${menuThumbnail(item)}<span>${esc(item.name)}</span></button>`;
    const results=()=>`<div class="mention-results" role="listbox" aria-label="素材引用候选">${shown.map(row).join('')||'<p class="mention-empty">没有匹配素材</p>'}</div>`;
    menu.innerHTML=`<div class="mention-categories"><label>${icon('search')}<input type="text" aria-label="搜索引用素材" placeholder="搜索" value="${esc(session.term)}"></label><div class="mention-menu-scroll">${term?results():`${linked.length?`<small>已引用</small><div class="mention-linked" role="listbox" aria-label="已引用素材">${linked.map((item,index)=>`<button type="button" role="option" aria-selected="${category==='all'&&index===active}" data-linked-index="${index}">${menuThumbnail(item)}<span>${esc(item.name)}</span></button>`).join('')}</div>`:''}<small>素材引用</small>${categories.map(([id,label,glyph])=>`<button type="button" data-mention-kind="${id}" aria-pressed="${category===id}" aria-expanded="${category===id}" aria-haspopup="listbox">${icon(glyph)}<span>${label}</span>${icon('chevronRight')}</button>`).join('')}`}</div></div>${!term&&category!=='all'?`<div class="mention-flyout"><small>${categories.find(item=>item[0]===category)[1]}</small>${results()}</div>`:''}`;
    menu.querySelectorAll('[data-linked-index]').forEach((button,index)=>{button.id=`mention-linked-${version}-${index}`;});
    menu.querySelectorAll('[data-mention-kind]').forEach((button,index)=>{
      button.id=`mention-category-${version}-${index}`;
      button.classList.toggle('mention-keyboard-active',rootView&&active===linked.length+index);
    });
    session.input.setAttribute('aria-activedescendant',rootView?active>=linked.length?`mention-category-${version}-${active-linked.length}`:`mention-linked-${version}-${active}`:shown.length?`mention-option-${version}-${active}`:'');
    menu.querySelector('input').oninput=event=>{
      if(event.isComposing)return;
      clearTimeout(categoryTimer);
      const field=event.target,caret=field.selectionStart;session.term=field.value;category='all';active=0;draw();const next=menu.querySelector('input');next.focus();next.setSelectionRange(caret,caret);
    };
    menu.querySelector('input').oncompositionend=event=>event.target.dispatchEvent(new Event('input',{bubbles:true}));
    menu.querySelectorAll('[data-mention-kind]').forEach(button=>button.onpointerenter=()=>{
      clearTimeout(categoryTimer);
      if(category===button.dataset.mentionKind)return;
      const next=button.dataset.mentionKind;
      categoryTimer=setTimeout(()=>{if(session){category=next;active=0;draw();}},120);
    });
    menu.querySelectorAll('[data-mention-kind]').forEach(button=>button.onclick=()=>{clearTimeout(categoryTimer);category=button.dataset.mentionKind;active=0;draw();});
    menu.querySelectorAll('[data-mention-index]').forEach(button=>button.onclick=()=>choose(Number(button.dataset.mentionIndex)));
    menu.querySelectorAll('[data-linked-index]').forEach(button=>button.onclick=()=>{clearTimeout(categoryTimer);shown=linked;choose(Number(button.dataset.linkedIndex));});
    menu.querySelector('.mention-menu-scroll').scrollTop=scroll;
    if(focused)menu.querySelector('input').focus({preventScroll:true});
    position();
  }
  async function choose(index){
    const current=session,item=shown[index];if(!current||!item)return;
    const input=current.input;
    if(input.readOnly||input.disabled){close();return;}
    try {
      // Validate/bind first. A failed reference must not leave a fake @ token.
      const token='@'+String(item.name||'素材').replace(/[\r\n]/g,' ');
      const apply=await onSelect(item,input,token);
      if(session!==current)return;
      if(!input.isConnected){close();return;}
      const suffix=input.value.slice(current.end),prefix=input.value.slice(0,current.start);
      const separator=current.hover?'':' ',value=prefix+token+separator+suffix;
      if(input.maxLength>0 && value.length>input.maxLength)throw new Error('引用后的文字超过输入长度限制');
      chosen.set(input,[...(chosen.get(input)||[]).filter(ref=>ref.token!==token),{...item,token}]);
      input.value=value;
      const caret=prefix.length+token.length+separator.length;
      close();input.focus({preventScroll:true});input.setSelectionRange(caret,caret);
      inserting=true;
      try {input.dispatchEvent(new Event('input',{bubbles:true}));}finally{inserting=false;}
      if(typeof apply==='function') await apply();
      editors.get(input)?.render();
    }catch(error){onError(error.message);}
  }
  async function update(input){
    const found=query(input);if(!found){if(session?.input===input)close();return;}
    const changed=session?.input!==input;
    if(changed){close();category='all';active=0;}
    session={input,...found};const request=++version;
    input.setAttribute('aria-autocomplete','list');input.setAttribute('aria-expanded','true');
    try {
      const result=await getItems(input);
      if(request!==version||!input.isConnected||![input,editors.get(input)?.editor].includes(document.activeElement))return;
      items=[...new Map(result.filter(Boolean).map(item=>[key(item),item])).values()];menu.hidden=false;draw();
    }catch(error){if(request===version){close();onError(error.message);}}
  }
  root.addEventListener('input',event=>{if(!inserting&&event.target.matches?.(selector)&&!event.isComposing)update(event.target);});
  root.addEventListener('compositionend',event=>{if(event.target.matches?.(selector))update(event.target);});
  root.addEventListener('click',event=>{if(event.target.matches?.(selector))update(event.target);});
  // Capture Enter before Agent's send handler. IME confirmation stays native.
  const keydown=event=>{
    if(!session||event.isComposing||menu.hidden)return;
    if(event.target!==session.input&&event.target!==editors.get(session.input)?.editor&&!menu.contains(event.target))return;
    if(['ArrowDown','ArrowUp','Enter','Tab','Escape','ArrowLeft','ArrowRight'].includes(event.key)){
      if(event.key==='ArrowLeft'&&category==='all')return;
      const rootView=!session.term&&category==='all';
      if(event.key==='ArrowRight'&&!(rootView&&active>=rootLinkedCount))return;
      event.preventDefault();event.stopImmediatePropagation();
      if(event.key==='ArrowLeft'){category='all';active=0;draw();return;}
      if(event.key==='Escape'){const input=session.input;close();input.focus({preventScroll:true});return;}
      if(['Enter','Tab','ArrowRight'].includes(event.key)){
        if(rootView&&active>=rootLinkedCount){category=categories[active-rootLinkedCount][0];active=0;draw();}else choose(active);
        return;
      }
      const count=rootView?rootLinkedCount+categories.length:shown.length;
      if(count){active=(active+(event.key==='ArrowDown'?1:-1)+count)%count;draw();menu.querySelector(rootView?active>=rootLinkedCount?`#mention-category-${version}-${active-rootLinkedCount}`:`#mention-linked-${version}-${active}`:`#mention-option-${version}-${active}`)?.scrollIntoView({block:'nearest'});}
    }
  };
  document.addEventListener('keydown',keydown,true);
  document.addEventListener('pointerdown',event=>{clearTimeout(hoverTimer);if(session&&!menu.contains(event.target)&&event.target!==session.input&&!editors.get(session.input)?.editor.contains(event.target))close();},true);
  menu.addEventListener('pointerleave',()=>{if(session?.hover)hoverTimer=setTimeout(close,220);});
  menu.addEventListener('pointerenter',()=>clearTimeout(hoverTimer));
  window.addEventListener('resize',position);
  document.addEventListener('scroll',()=>{if(session&&!menu.contains(document.activeElement))position();},true);
  function removeReference(assetId, input) {
    for (const field of input ? [input] : root.querySelectorAll(selector)) {
      const refs=references(field).filter(ref=>ref.token).sort((a,b)=>b.token.length-a.token.length);
      let value='',offset=0;
      while(offset<field.value.length){
        let next=null,at=field.value.length;
        for(const ref of refs){const found=field.value.indexOf(ref.token,offset);if(found>=0&&found<at){next=ref;at=found;}}
        value+=field.value.slice(offset,at);
        if(!next)break;
        if(next.assetId!==assetId)value+=next.token;
        offset=at+next.token.length;
      }
      chosen.set(field,(chosen.get(field)||[]).filter(ref=>ref.assetId!==assetId));
      field.value=value;
      field.dispatchEvent(new Event('input',{bubbles:true}));
      editors.get(field)?.render();
    }
    close();
  }
  return {close,removeReference,refresh(){scan();root.querySelectorAll(selector).forEach(input=>editors.get(input)?.render());},open(input){input.focus({preventScroll:true});const p=input.selectionStart??input.value.length;input.setRangeText('@',p,input.selectionEnd??p,'end');editors.get(input)?.render();input.dispatchEvent(new Event('input',{bubbles:true}));}};
}

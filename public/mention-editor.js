// The textarea remains the serialized draft/API boundary; the visible editor
// renders immutable reference tokens while keeping normal text freely editable.
export function createMentionEditor(input,{references,visual,thumbnail,onHover,onLeave,onChange,onKey,onPreview}) {
  const editor=document.createElement('div');
  editor.className='mention-editor';editor.dataset.mentionEditor=input.id||input.className;
  editor.setAttribute('role','textbox');editor.setAttribute('aria-multiline','true');
  editor.setAttribute('aria-label',input.getAttribute('aria-label')||input.labels?.[0]?.textContent||'提示词');
  input.after(editor);input.classList.add('mention-backing');
  const strip=input.matches('#detailPromptInput,[data-script-storyboard],[data-script-motion]')?document.createElement('div'):null;
  if(strip){strip.className='mention-reference-strip';editor.before(strip);}
  input.tabIndex=-1;input.setAttribute('aria-hidden','true');editor.tabIndex=0;
  const descriptor=Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value');
  let editing=false,composing=false,last='';
  const serialize=node=>{
    if(node===editor&&node.childNodes.length===1&&node.firstChild.nodeName==='BR')return '';
    if(node.nodeType===Node.TEXT_NODE)return node.data;
    if(node.nodeType!==Node.ELEMENT_NODE&&node.nodeType!==Node.DOCUMENT_FRAGMENT_NODE)return '';
    if(node.dataset?.mentionEnd)return '';
    if(node.dataset?.mentionToken)return node.dataset.mentionToken;
    if(node.nodeName==='BR')return '\n';
    return [...node.childNodes].map(serialize).join('');
  };
  function caret(){
    const selection=getSelection();if(!selection.rangeCount||!editor.contains(selection.anchorNode))return input.selectionStart;
    const range=selection.getRangeAt(0).cloneRange();range.selectNodeContents(editor);range.setEnd(selection.anchorNode,selection.anchorOffset);
    return serialize(range.cloneContents()).length;
  }
  function place(offset){
    let remaining=offset,found=false;const range=document.createRange();
    function visit(node){
      for(const child of node.childNodes){
        const length=serialize(child).length;
        if(remaining>length){remaining-=length;continue;}
        if(child.nodeType===Node.TEXT_NODE){range.setStart(child,Math.min(remaining,length));found=true;return;}
        if(child.dataset?.mentionToken){remaining?range.setStartAfter(child):range.setStartBefore(child);found=true;return;}
        visit(child);if(found)return;
      }
    }
    visit(editor);if(!found){range.selectNodeContents(editor);range.collapse(false);}else range.collapse(true);
    const selection=getSelection();selection.removeAllRanges();selection.addRange(range);
  }
  function renderStrip(refs,value){
    if(!strip)return;
    const seen=new Set(),active=refs.filter(ref=>{if(!ref.assetId||!value.includes(ref.token)||seen.has(ref.assetId))return false;seen.add(ref.assetId);return true;});
    const signature=JSON.stringify(active);if(strip.dataset.signature===signature)return;strip.dataset.signature=signature;
    strip.replaceChildren();
    for(const ref of active){const card=document.createElement('span');card.className='mention-preview-card';card.title=ref.name;if(ref.kind==='audio')card.dataset.audioPreview=ref.url||'';card.innerHTML=thumbnail(ref);strip.append(card);}
  }
  function render(){
    editor.contentEditable=String(!input.readOnly&&!input.disabled);editor.setAttribute('aria-readonly',String(input.readOnly));
    editor.dataset.placeholder=input.placeholder;
    if(editing||composing)return;
    const refs=references(),value=input.value,signature=value+JSON.stringify(refs.map(r=>[r.token,r.assetId,r.name,r.url,r.number,r.pending]));
    if(signature===last)return;last=signature;
    renderStrip(refs,value);
    const focused=document.activeElement===editor,offset=focused?caret():0;
    editor.replaceChildren();
    const tokens=refs.filter(r=>r.token).sort((a,b)=>b.token.length-a.token.length);
    let position=0;
    while(position<value.length){
      let next=null,at=value.length;
      for(const ref of tokens){const index=value.indexOf(ref.token,position);if(index>=0&&index<at){at=index;next=ref;}}
      if(at>position)editor.append(document.createTextNode(value.slice(position,at)));
      if(!next)break;
      const chip=document.createElement('span');chip.className='mention-token';chip.contentEditable='false';chip.dataset.mentionToken=next.token;
      chip.dataset.assetId=next.assetId||'';chip.dataset.nodeId=next.nodeId||'';chip.dataset.pending=String(!!next.pending);chip.title=next.name+(next.pending?' · 待生成':'');chip.setAttribute('aria-label',chip.title);
      chip.innerHTML=visual(next);chip.onpointerenter=()=>onHover(input,chip,next);
      chip.onpointerleave=onLeave;
      if(onPreview&&next.url&&['image','prop','character'].includes(next.kind)){
        chip.dataset.imagePreview='true';chip.tabIndex=0;chip.setAttribute('role','button');chip.setAttribute('aria-label',`放大预览 ${next.name||'参考图片'}`);
        const preview=event=>{event.preventDefault();event.stopPropagation();onPreview(next);};
        chip.onclick=preview;
        chip.onkeydown=event=>{if(!event.isComposing&&['Enter',' '].includes(event.key))preview(event);};
      }
      editor.append(chip);position=at+next.token.length;
    }
    if(!editor.lastChild||editor.lastChild.nodeType!==Node.TEXT_NODE)editor.append(document.createTextNode(''));
    if(value.endsWith('\n')){const end=document.createElement('br');end.dataset.mentionEnd='true';editor.append(end);}
    if(focused)place(offset);
  }
  Object.defineProperty(input,'value',{configurable:true,get(){return descriptor.get.call(input);},set(value){descriptor.set.call(input,value);render();}});
  function sync(){
    if(composing)return;
    const value=serialize(editor);if(input.maxLength>0&&value.length>input.maxLength){last='';render();return;}
    const offset=caret();editing=true;input.value=value;input.setSelectionRange(offset,offset);
    input.dispatchEvent(new Event('input',{bubbles:true}));editing=false;last='';renderStrip(references(),value);onChange(input);
  }
  function insert(text){
    if(input.readOnly||input.disabled)return;
    const selection=getSelection();if(!selection.rangeCount)return;
    const range=selection.getRangeAt(0);range.deleteContents();const node=document.createTextNode(text);range.insertNode(node);
    if(serialize(editor).endsWith('\n')&&!editor.querySelector('[data-mention-end]')){const end=document.createElement('br');end.dataset.mentionEnd='true';editor.append(end);}
    range.setStart(node,node.length);range.collapse(true);selection.removeAllRanges();selection.addRange(range);sync();
  }
  editor.addEventListener('input',sync);
  editor.addEventListener('compositionstart',()=>composing=true);
  editor.addEventListener('compositionend',()=>{composing=false;sync();});
  editor.addEventListener('paste',event=>{event.preventDefault();insert(event.clipboardData.getData('text/plain'));last='';render();});
  for(const type of ['copy','cut'])editor.addEventListener(type,event=>{
    const selection=getSelection();if(!selection.rangeCount||selection.isCollapsed)return;
    event.preventDefault();const range=selection.getRangeAt(0);event.clipboardData.setData('text/plain',serialize(range.cloneContents()));
    if(type==='cut'&&!input.readOnly&&!input.disabled){range.deleteContents();sync();}
  });
  editor.addEventListener('keydown',event=>{
    if(event.isComposing)return;
    onKey(event);
    if(!event.defaultPrevented&&event.key==='Enter'){event.preventDefault();insert('\n');}
  });
  function rememberSelection(){
    const selected=getSelection();if(!selected.rangeCount||!editor.contains(selected.anchorNode))return;
    const range=selected.getRangeAt(0),before=range.cloneRange();before.selectNodeContents(editor);before.setEnd(range.startContainer,range.startOffset);
    const start=serialize(before.cloneContents()).length;before.setEnd(range.endContainer,range.endOffset);
    selection(start,serialize(before.cloneContents()).length);
  }
  editor.addEventListener('click',()=>{rememberSelection();onChange(input);});
  editor.addEventListener('keyup',rememberSelection);
  input.addEventListener('input',render);
  input.addEventListener('focus',()=>{render();editor.focus({preventScroll:true});place(input.selectionStart);});
  const selection=input.setSelectionRange.bind(input);
  input.setSelectionRange=(start,end,...rest)=>{selection(start,end,...rest);if(document.activeElement===editor&&!editing)place(start);};
  new MutationObserver(render).observe(input,{attributes:true,attributeFilter:['readonly','disabled','placeholder']});
  render();return {editor,render,caretRect(){
    const selection=getSelection();
    if(!selection.rangeCount||!editor.contains(selection.anchorNode))return null;
    const range=selection.getRangeAt(0).cloneRange();range.collapse(true);
    const rect=range.getClientRects()[0];return rect?.height?rect:null;
  },offset(anchor){const range=document.createRange();range.selectNodeContents(editor);range.setEndBefore(anchor);return serialize(range.cloneContents()).length;},focus(){editor.focus({preventScroll:true});place(input.selectionStart);}};
}

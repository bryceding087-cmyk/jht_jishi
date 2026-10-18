import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { installCanvasConnections, portAttraction, connectionTargetGeometry } from '../public/canvas-connections.js';

const app = readFileSync(new URL('../public/app.js',import.meta.url),'utf8');
const connectSource = app.slice(app.indexOf('function connectionProblem('),app.indexOf('function startNodeDrag('));
class Element {
  constructor(kind,rect={},parent=null,dataset={}) {
    Object.assign(this,{kind,rect,parent,dataset,isConnected:true,events:new Map(),values:new Map(),classes:new Set()});
    this.style={setProperty:(key,value)=>this.values.set(key,value),removeProperty:key=>this.values.delete(key)};
    this.classList={add:(...names)=>names.forEach(name=>this.classes.add(name)),remove:(...names)=>names.forEach(name=>this.classes.delete(name)),contains:name=>this.classes.has(name)};
  }
  closest(selector) {
    for (let el=this;el;el=el.parent) {
      if (selector.split(',').some(item=>item==='.node' && el.kind==='node' || item==='[data-node-port]' && el.kind==='port' || item===`[data-port="${el.dataset.port}"]` && el.kind==='port' || item==='.workspace-chrome' && el.kind==='chrome')) return el;
    }
    return null;
  }
  contains(el) { for (;el;el=el.parent) if (el===this) return true; return false; }
  querySelector(selector) { return selector==='.frame-stage'?this.frame:selector==='[data-port="in"]'?this.input:null; }
  getBoundingClientRect() { return this.rect; }
  addEventListener(name,fn) { if(!this.events.has(name))this.events.set(name,[]);this.events.get(name).push(fn); }
  emit(name,values={}) {
    const event={target:this,clientX:0,clientY:0,button:0,isPrimary:true,pointerId:1,pointerType:'mouse',
      preventDefault(){this.prevented=true;},stopImmediatePropagation(){this.stopped=true;},...values};
    for(const fn of this.events.get(name)||[]){fn(event);if(event.stopped)break;}
    return event;
  }
}
const rect=(left,top,width,height)=>({left,top,width,height,right:left+width,bottom:top+height});
function setup({zoom=1,reduced=false}={}) {
  const win=new Element('window'),doc=new Element('document'),canvas=new Element('canvas',rect(20,30,1200,800));
  const frames=new Map(),timeouts=[],media=new Element('media');let nextFrame=0,captured=null,hit=null,saves=0,renders=0;
  media.matches=reduced;win.matchMedia=()=>media;win.performance={now:()=>1000};
  win.requestAnimationFrame=fn=>{frames.set(++nextFrame,fn);return nextFrame;};win.cancelAnimationFrame=id=>frames.delete(id);
  win.setTimeout=fn=>timeouts.push(fn);doc.defaultView=win;canvas.ownerDocument=doc;doc.elementFromPoint=()=>hit;
  canvas.setPointerCapture=id=>{captured=id;};canvas.hasPointerCapture=id=>captured===id;canvas.releasePointerCapture=()=>{captured=null;canvas.emit('lostpointercapture');};
  const state={view:'canvas',canvasTool:'move',spaceDown:false,zoom,panX:40,panY:-20,edges:[],pendingPort:null,
    nodes:[{id:'source',type:'prompt',x:100,y:120},{id:'target',type:'video',x:500,y:140}],assets:[]};
  const source=new Element('node',rect(100,120,300,250),canvas,{node:'source'});
  const output=new Element('port',rect(390,180,64*zoom,132*zoom),source,{nodePort:'source',port:'out'});
  const target=new Element('node',rect(500,140,360,240),canvas,{node:'target'});
  target.frame=new Element('frame',target.rect,target);target.input=new Element('port',rect(446,194,64,132),target,{nodePort:'target',port:'in'});
  const errors=[];let controller;
  const context=vm.createContext({state,nodeAsset:node=>state.assets.find(a=>a.id===node?.assetId),
    isMediaNode:node=>['asset','prop','video-asset','audio'].includes(node?.type),prepareVideoReference:async()=>{},
    toast:message=>errors.push(message),updatePortHighlights:()=>controller?.sync(),renderEdges:()=>renders++,
    loadNodeSettings:()=>{},scheduleSave:()=>saves++});
  vm.runInContext(connectSource+'\nglobalThis.connect=connectPort;globalThis.problem=connectionProblem;',context);
  controller=installCanvasConnections({canvas,state,canConnect:id=>!context.problem(state.nodes.find(n=>n.id===state.pendingPort),state.nodes.find(n=>n.id===id)),
    connect:context.connect,renderEdges:()=>renders++,highlights:()=>controller?.sync(),onError:error=>errors.push(error)});
  return {state,canvas,doc,win,media,source,output,target,errors,controller,context,
    get saves(){return saves;},get captures(){return captured;},
    frame(){for(const [id,fn] of [...frames]){frames.delete(id);fn();}},hit(value){hit=value;},
    point(name,element,x,y,extra={}){hit=element;return canvas.emit(name,{target:element,clientX:x,clientY:y,...extra});}};
}
const settle=()=>new Promise(resolve=>setImmediate(resolve));

// Attraction is bounded in world units at both minimum and maximum zoom.
for(const zoom of [.05,.5,1,2,8]) {
  const bounds=rect(30,40,64*zoom,132*zoom),point={x:bounds.left+50*zoom,y:bounds.top+110*zoom};
  const delta=portAttraction(bounds,point,zoom);
  assert.equal(delta.x,10);assert.ok(Math.abs(delta.y-33)<1e-8);
}
const top=connectionTargetGeometry(rect(100,100,400,300),{x:150,y:105});
const bottom=connectionTargetGeometry(rect(100,100,400,300),{x:450,y:395});
assert.equal(top.anchor.y,100);assert.equal(bottom.anchor.y,400);
assert.ok(top.rotateX>0 && bottom.rotateX<0);assert.ok(top.rotateY<0 && bottom.rotateY>0);

// Real pointer drag -> target feedback -> existing connection/persistence code.
{
  const env=setup({zoom:2}),before=JSON.stringify(env.state.nodes);
  env.point('pointermove',env.output,480,420);env.frame();
  assert.notEqual(env.output.values.get('--port-y'),'0px');
  env.canvas.emit('pointerout',{target:env.output,relatedTarget:env.canvas});
  assert.equal(env.output.values.size,0);
  env.point('pointerdown',env.output,430,250);
  env.point('pointermove',env.target,505,160);env.frame();
  assert.equal(env.state.pendingPort,'source');assert.equal(env.captures,1);
  assert.ok(env.target.classes.has('connection-target'));
  assert.deepEqual(env.state.wireCursor,{x:(500-20-40)/2,y:(160-30+20)/2});
  assert.equal(env.state.edges.length,0);
  env.point('pointerup',env.target,505,160);await settle();
  assert.equal(env.captures,null);assert.equal(env.state.edges.length,1);assert.equal(env.saves,1);
  assert.equal(env.state.pendingPort,null);assert.ok(env.target.classes.has('connection-landed'));
  assert.ok(!env.target.classes.has('connection-target'));assert.equal(JSON.stringify(env.state.nodes),before);
  env.point('click',env.target,505,160);await settle();assert.equal(env.state.edges.length,1);
  env.point('pointerdown',env.output,430,250);
  assert.equal(env.state.suppressClickUntil,0,'the next intentional gesture must not be swallowed by previous drag suppression');
  env.controller.reset();
}

// Stationary clicks remain available to existing port click/keyboard handlers.
{
  const env=setup();env.point('pointerdown',env.output,420,230);env.point('pointerup',env.output,420,230);
  assert.equal(env.state.pendingPort,null);assert.equal(env.captures,null);
  assert.ok(!env.point('click',env.output,420,230).stopped);
  await env.context.connect('source','out');
  assert.ok(env.point('pointerdown',env.target,700,300).stopped,'click-to-connect must not start a node drag');
  env.point('click',env.target,700,300);await settle();assert.equal(env.state.edges.length,1);
}

for(const cancel of ['empty','pointercancel','lostpointercapture','escape','blur','hidden','reset']) {
  const env=setup();env.point('pointerdown',env.output,420,230);env.point('pointermove',env.target,505,160);env.frame();
  if(cancel==='empty')env.point('pointerup',env.canvas,950,600);
  if(cancel==='pointercancel'||cancel==='lostpointercapture')env.canvas.emit(cancel);
  if(cancel==='escape')env.win.emit('keydown',{key:'Escape'});
  if(cancel==='blur')env.win.emit('blur');
  if(cancel==='hidden'){env.doc.hidden=true;env.doc.emit('visibilitychange');}
  if(cancel==='reset')env.controller.reset();
  assert.equal(env.state.edges.length,0,cancel);assert.equal(env.state.pendingPort,null,cancel);
  assert.equal(env.captures,null,cancel);assert.ok(!env.target.classes.has('connection-target'),cancel);
}

{
  const env=setup();env.point('pointerdown',env.output,420,230);
  env.win.emit('pointerup',{target:env.win});
  env.point('pointermove',env.target,505,160);env.frame();
  assert.equal(env.state.pendingPort,null,'releasing outside before drag threshold must clear the pending gesture');
}

// Invalid destinations, self connections, chrome and secondary pointers do not connect.
{
  const env=setup();env.state.nodes[0].type='video';env.state.nodes[1].type='asset';
  env.point('pointerdown',env.output,420,230);env.point('pointermove',env.target,505,160);env.frame();
  assert.ok(!env.target.classes.has('connection-target'));
  env.point('pointerup',env.target,505,160);await settle();assert.equal(env.state.edges.length,0);
  env.state.canvasTool='hand';env.point('pointerdown',env.output,420,230);env.point('pointermove',env.target,505,160);env.frame();
  assert.equal(env.state.pendingPort,null);
  env.state.canvasTool='move';env.point('pointerdown',env.output,420,230,{isPrimary:false});
  env.point('pointermove',env.target,505,160,{pointerId:2});env.frame();assert.equal(env.state.pendingPort,null);
}
for (const destination of ['self','chrome']) {
  const env=setup();env.source.input={};
  const hit=destination==='self'?env.source:new Element('chrome',env.target.rect,env.target);
  env.point('pointerdown',env.output,420,230);env.point('pointermove',hit,505,160);env.frame();
  assert.ok(!env.source.classes.has('connection-target'));assert.ok(!env.target.classes.has('connection-target'));
  env.point('pointerup',hit,505,160);await settle();assert.equal(env.state.edges.length,0);
}

// Reduced motion keeps feedback and the larger hit area without movement.
{
  const env=setup({reduced:true});env.point('pointermove',env.output,440,270);env.frame();
  assert.equal(env.output.values.size,0);
  env.point('pointerdown',env.output,420,230);env.point('pointermove',env.target,505,160);env.frame();
  assert.equal(env.target.values.get('--connection-rx'),'0deg');assert.ok(env.target.classes.has('connection-target'));
  env.controller.reset();
}

// Video preparation can await network/media work; Escape and a second gesture
// must not commit a stale edge after that asynchronous step finishes.
{
  const env=setup();env.state.nodes[0].type='video';
  let resolve;env.context.prepareVideoReference=()=>new Promise(done=>{resolve=done;});
  env.point('pointerdown',env.output,420,230);env.point('pointermove',env.target,505,160);env.frame();
  env.point('pointerup',env.target,505,160);
  assert.ok(env.point('pointerdown',env.output,420,230).stopped);
  env.win.emit('keydown',{key:'Escape'});resolve();await settle();
  assert.equal(env.state.edges.length,0);assert.equal(env.saves,0);
}
{
  const env=setup();env.state.edges.push({from:'source',to:'target'});
  env.point('pointerdown',env.output,420,230);env.point('pointermove',env.target,505,160);env.frame();
  env.point('pointerup',env.target,505,160);await settle();
  assert.equal(env.state.edges.length,1);assert.equal(env.saves,0);
  env.point('pointermove',env.output,440,270);env.canvas.emit('pointerleave');env.frame();
  assert.equal(env.output.values.size,0,'a queued frame cannot resurrect attraction after leaving');
}

console.log('PASS: magnetic ports at 5–800% zoom, real drag/click connections and saving, target tilt, cancellation, invalid inputs, reduced motion, stable node coordinates and duplicate-click protection');

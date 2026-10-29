import assert from 'node:assert/strict';
import { compactStudioGroups, contentBounds } from '../public/canvas-groups.js';

const size=node=>({width:node.width||336,height:node.height||230});
const fixture=(role='video',step=360)=>{
  const nodes=Array.from({length:9},(_,i)=>({id:String(i),x:104+i%4*460,y:134+Math.floor(i/4)*step,
    mujian:{runId:'run',role,assetType:role==='reference'?'character':undefined}}));
  return {nodes,groups:[{id:'group',x:80,y:80,width:1864,height:1134,node_ids:nodes.map(n=>n.id)}]};
};
for(const [role,step] of [['video',360],['reference',500],['firstFrame',380]]){
  const {nodes,groups}=fixture(role,step);nodes[0].width=300;nodes[0].height=334;
  assert.equal(compactStudioGroups(groups,nodes,size),true);
  assert.equal(nodes[1].x-nodes[0].x,332);
  assert.equal(nodes[4].y-nodes[0].y,366);
  assert.ok(groups[0].width<1864&&groups[0].height<1134);
  const bounds=contentBounds(groups[0],nodes,size);
  for(const node of nodes){
    assert.ok(node.x>=bounds.x+24&&node.y>=bounds.y+24);
    assert.ok(node.x+size(node).width<=bounds.x+bounds.width-24);
  }
  const saved=structuredClone({groups,nodes});
  assert.equal(compactStudioGroups(groups,nodes,size),false);
  assert.deepEqual({groups,nodes},saved);
}
for(const mutate of [nodes=>nodes[2].x+=10,nodes=>delete nodes[2].mujian,nodes=>nodes[2].mujian.runId='other']){
  const graph=fixture();mutate(graph.nodes);const saved=structuredClone(graph);
  assert.equal(compactStudioGroups(graph.groups,graph.nodes,size),false);
  assert.deepEqual(graph,saved);
}
console.log('PASS: compact legacy Agent groups, mixed media sizes, no repeat migration, manual layouts preserved');

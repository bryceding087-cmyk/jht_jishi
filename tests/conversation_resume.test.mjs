import assert from 'node:assert/strict';
import { createConversationResume } from '../public/conversation-resume.js';

const data = new Map();
const storage = {getItem:key=>data.get(key), setItem:(key,value)=>data.set(key,value), removeItem:key=>data.delete(key)};
const resume = createConversationResume({storage:()=>storage});
const user = {id:1}, other = {id:2};
const finished = {id:'completed', owner_id:'1', status:'done', updated_at:5};
const active = {id:'running', owner_id:'1', status:'running', active_task:{id:'job'}, updated_at:2};
const foreign = {id:'private', owner_id:'2', status:'running', updated_at:20};

// A new iframe has no in-memory drafts, but can discover the owner's task.
assert.equal(resume.target(user, [finished, foreign, active]), 'running');
resume.remember(user, active.id);
assert.equal(resume.target(user, [finished, active]), active.id);
// Completion while away still opens the original conversation.
assert.equal(resume.target(user, [finished, {...active, status:'done', active_task:null}]), active.id);
// Independently running conversations remain discoverable.
resume.remember(user, finished.id);
assert.equal(resume.target(user, [finished, active]), active.id);
// Explicit navigation, embedded canvases and another user's browser state win.
for (const query of ['conversation=completed', 'project=canvas', 'view=home', 'mode=canvas', 'embedded=1']) {
  assert.equal(resume.target(user, [active], new URLSearchParams(query)), null);
}
assert.equal(resume.target(other, [finished]), null);
assert.equal(resume.target({...user, role:'admin'}, [foreign]), null);
assert.equal(resume.target(null, [active]), null);
// Deleted/foreign IDs in storage must not cause an unauthorized GET.
resume.remember(user, foreign.id);
assert.equal(resume.target(user, [foreign, finished]), null);
assert.equal(data.get('jht.conversation-return.v1:1'), undefined);
resume.remember(user, 'deleted');
assert.equal(resume.target(user, [active]), active.id);
// Third-party storage can be blocked; server discovery still works.
const blocked = createConversationResume({storage:()=>{throw new Error('Storage blocked');}});
blocked.remember(user, finished.id);
blocked.forget(user);
assert.equal(blocked.target(user, [foreign, active]), active.id);
assert.ok([...data.values()].every(value=>typeof value==='string'), 'only conversation IDs are stored');
console.log('PASS: conversation return, completion, explicit navigation, ownership, deletion and blocked storage');

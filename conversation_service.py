"""Conversation creation contexts, stored independently of canvas projects."""
import json
import time
import uuid
from typing import Literal
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field
from conversation_titles import short_title


class ConversationMetadata(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    pinned: bool | None = None


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=80)
    kind: Literal['image', 'video', 'audio']
    prompt: str = Field(min_length=1, max_length=4000)
    settings: dict = Field(default_factory=dict)
    request_id: str = Field(min_length=1, max_length=160)


def create_context(h, name, kind, owner, *, context_id=None, nodes=None, prompt='', settings=None):
    automatic = name is None
    name = short_title(prompt, kind) if automatic else name
    return h.save('conversation', {'id':context_id or 'conversation-'+str(uuid.uuid4()),
        'name':name, 'title':name, 'creation_type':'conversation', 'kind':kind, 'owner_id':str(owner),
        'title_source': 'initial' if automatic else 'manual',
        'prompt':prompt, 'settings':settings or {}, 'nodes':nodes or [], 'edges':[], 'groups':[],
        'revision':1, 'created_at':time.time(), 'updated_at':time.time()})


def refine_initial_title(h, cid, suggestion):
    """Merge once, atomically with manual renames and generation updates."""
    if not isinstance(suggestion, str) or not suggestion.strip():
        return None
    with h.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT body FROM records WHERE kind='conversation' AND id=?", (cid,)).fetchone()
        value = json.loads(row[0]) if row else None
        if not value or value.get('deleted_at') or value.get('title_source') != 'initial':
            return None
        title = short_title(suggestion, value.get('kind'), summarize=False)
        value.update(name=title, title=title, title_source='generated', revision=value.get('revision', 0) + 1)
        db.execute("UPDATE records SET body=? WHERE kind='conversation' AND id=?", (json.dumps(value, ensure_ascii=False), cid))
        return title


def conversation_covers(h, context_ids):
    """Latest generated visual output per conversation; never use input references."""
    covers = {}
    assets = h.listing('asset')
    deleted_urls = {a.get('url') for a in assets if a.get('deleted_at')}

    def consider(cid, kind, url, timestamp):
        if cid not in context_ids or kind not in ('image', 'prop', 'video') or not url or url in deleted_urls:
            return
        if timestamp >= covers.get(cid, {}).get('created_at', -1):
            covers[cid] = {'kind': 'image' if kind == 'prop' else kind, 'url': url, 'created_at': timestamp}

    for asset in assets:
        if not asset.get('deleted_at') and asset.get('source') == 'generated':
            consider(asset.get('project_id'), asset.get('kind'), asset.get('url'), asset.get('created_at', 0))
    # Video tasks have playable output before the user explicitly saves it as an asset.
    for task in h.listing('task'):
        if not task.get('deleted_at') and task.get('status') in ('done', 'succeeded'):
            consider(task.get('project_id'), 'video', task.get('video_url'), task.get('updated_at') or task.get('created_at', 0))
    return covers


def conversation_activity(h, context_ids):
    """Summarize persisted media jobs without submitting or polling a provider."""
    activity = {}
    for record_kind, kind in (('task', 'video'), ('image_task', 'image'), ('audio_task', 'audio')):
        for task in h.listing(record_kind):
            cid = task.get('project_id')
            if cid not in context_ids or task.get('deleted_at'):
                continue
            status = task.get('status', '')
            if task.get('local_stop_requested'):
                status = 'stopped'
            elif (kind == 'video' and task.get('provider_task_id') and task.get('polling_interrupted')
                    and status not in ('done', 'succeeded')):
                status = 'running'
            active = status in ('queued', 'running', 'waiting_tool')
            stamp = task.get('updated_at') or task.get('created_at') or 0
            rank = (active, task.get('created_at') or 0, stamp, task['id'])
            previous = activity.get(cid)
            latest = max(stamp, previous['updated_at'] if previous else 0)
            if not previous or rank > previous['_rank']:
                activity[cid] = {'_rank': rank, 'status': status, 'updated_at': latest,
                    'active_task': {'id': task['id'], 'kind': kind, 'status': status} if active else None}
            else:
                previous['updated_at'] = latest
    return {cid: {k: v for k, v in item.items() if k != '_rank'} for cid, item in activity.items()}


def install_conversations(app, h):
    router=APIRouter(prefix='/api/conversations')

    def authorized(value, request):
        return request.state.user.get('role') == 'admin' or h.record_owner(value) == str(request.state.user['id'])

    @app.delete('/api/recent-items/{kind}/{cid}', status_code=204)
    def delete_recent(kind: str, cid: str, request: Request):
        if kind not in ('project', 'conversation'):
            raise HTTPException(404, '项目不存在')
        # Retain output assets and in-flight task records; only the creation
        # context becomes permanently inaccessible, with no recycle-bin entry.
        now=time.time()
        with h.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row=db.execute('SELECT body FROM records WHERE kind=? AND id=?',(kind,cid)).fetchone()
            if not row:raise HTTPException(404,'项目不存在')
            value=json.loads(row[0])
            if not authorized(value, request):
                raise HTTPException(404,'项目不存在')
            value.update(deleted_at=now,permanently_deleted=True,updated_at=now,revision=value.get('revision',0)+1)
            db.execute('UPDATE records SET body=? WHERE kind=? AND id=?',(json.dumps(value,ensure_ascii=False),kind,cid))
            for rid,body in db.execute("SELECT id,body FROM records WHERE kind='agent_run'").fetchall():
                run=json.loads(body)
                if run.get('project_id')!=cid or not h.can_access('agent_run', run):continue
                run.update(deleted_at=now,status='cancelled',stop_requested=True,updated_at=now)
                db.execute("UPDATE records SET body=? WHERE kind='agent_run' AND id=?",(json.dumps(run,ensure_ascii=False),rid))

    def owned(cid, request):
        value=h.get('conversation',cid)
        if not authorized(value, request):
            raise HTTPException(404,'对话不存在')
        if value.get('deleted_at'):
            raise HTTPException(404, '对话不存在')
        return value

    def public(value):
        run=h.raw_record('agent_run',value['run_id']) if value.get('run_id') else None
        return {k:value.get(k) for k in ('id','title','name','kind','creation_type','prompt','settings','created_at','updated_at','pinned','owner_id','creator_name','creator_username')} | {
            'run_id':run['id'] if run else None,'status':run['status'] if run else '',
            'canvas_project_id':(run.get('studio') or {}).get('canvas_project_id') if run else None,
            'updated_at':max(value.get('updated_at',0),run['updated_at'] if run else 0)}

    @router.get('')
    def conversations(request:Request, trashed: bool = False):
        items = [public(c) for c in h.listing('conversation') if not c.get('permanently_deleted')
                 and (bool(c.get('trashed_at')) if trashed else not c.get('deleted_at')) and authorized(c, request)]
        covers = conversation_covers(h, {c['id'] for c in items})
        activity = conversation_activity(h, {c['id'] for c in items if c['kind'] in ('image', 'video', 'audio')})
        for item in items:
            generation = activity.get(item['id'], {})
            item.update(status=generation.get('status', item['status']), active_task=generation.get('active_task'),
                updated_at=max(item['updated_at'], generation.get('updated_at', 0)), cover=covers.get(item['id']))
        return sorted(items, key=lambda c:c['updated_at'], reverse=True)

    @router.get('/{cid}/info')
    def conversation_info(cid: str, request: Request):
        value = owned(cid, request)
        return public(value) | h.record_creator(value, legacy_owner=True)

    def recycle(cid, request, action):
        now = time.time()
        with h.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT body FROM records WHERE kind='conversation' AND id=?", (cid,)).fetchone()
            value = json.loads(row[0]) if row else None
            if not value or not authorized(value, request) or value.get('permanently_deleted'):
                raise HTTPException(404, '对话不存在')
            if action in ('restore', 'purge') and not value.get('trashed_at'):
                raise HTTPException(409, '对话不在回收站中')
            if action == 'trash' and value.get('deleted_at'):
                raise HTTPException(409, '对话已被删除')
            if action == 'restore':
                value.pop('deleted_at', None)
                value.pop('trashed_at', None)
            else:
                value.update(deleted_at=now, trashed_at=now)
                if action == 'purge':
                    value['permanently_deleted'] = True
            value.update(updated_at=now, revision=value.get('revision', 0) + 1)
            db.execute("UPDATE records SET body=? WHERE kind='conversation' AND id=?", (json.dumps(value, ensure_ascii=False), cid))
            # Preserve generated assets and history; restoring never restarts a model request.
            for rid, body in db.execute("SELECT id,body FROM records WHERE kind='agent_run'").fetchall():
                run = json.loads(body)
                if run.get('project_id') != cid or not h.can_access('agent_run', run):
                    continue
                if action == 'restore':
                    run.pop('deleted_at', None)
                else:
                    run.update(deleted_at=now, status='cancelled', stop_requested=True)
                run['updated_at'] = now
                db.execute("UPDATE records SET body=? WHERE kind='agent_run' AND id=?", (json.dumps(run, ensure_ascii=False), rid))
        return public(value)

    @router.delete('/{cid}', status_code=204)
    def trash_conversation(cid: str, request: Request, permanent: bool = False):
        recycle(cid, request, 'purge' if permanent else 'trash')

    @router.post('/{cid}/restore')
    def restore_conversation(cid: str, request: Request):
        return recycle(cid, request, 'restore')

    @router.post('',status_code=201)
    def create(body:ConversationCreate,request:Request):
        with h.manual_generation.submission(body, 'new'):
            return create_unlocked(body, request)

    def create_unlocked(body:ConversationCreate,request:Request):
        h.validate_private_references(body.settings)
        cid='conversation-'+str(uuid.uuid5(uuid.NAMESPACE_URL,f"{request.state.user['id']}:{body.request_id}"))
        old=h.raw_record('conversation',cid)
        if old:
            owned(cid,request)
            if any(old.get(key)!=value for key,value in {'kind':body.kind,'prompt':body.prompt,'settings':body.settings}.items()):
                raise HTTPException(409,'该请求编号已用于其他对话内容')
            return public(old)
        nodes=[{'id':'conversation-video','type':'video','title':'视频生成','settings':body.settings}] if body.kind=='video' else []
        return public(create_context(h,body.title,body.kind,request.state.user['id'],context_id=cid,nodes=nodes,prompt=body.prompt,settings=body.settings))

    @router.get('/generation-state')
    def generation_state(conversation_id: str | None = None):
        return h.manual_generation.state(conversation_id)

    @router.patch('/{cid}/metadata')
    def metadata(cid: str, body: ConversationMetadata, request: Request):
        # Read and merge in one transaction to preserve concurrent generation data.
        with h.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute("SELECT body FROM records WHERE kind='conversation' AND id=?", (cid,)).fetchone()
            if not row:
                raise HTTPException(404, '对话不存在')
            value = json.loads(row[0])
            if not authorized(value, request) or value.get('deleted_at'):
                raise HTTPException(404, '对话不存在')
            if body.title is not None:
                title = body.title.strip()
                if not title:
                    raise HTTPException(422, '名称不能为空')
                value.update(title=title, name=title, title_source='manual')
            if body.pinned is not None:
                value['pinned'] = body.pinned
            value.update(updated_at=time.time(), revision=value.get('revision', 0) + 1)
            db.execute("UPDATE records SET body=? WHERE kind='conversation' AND id=?", (json.dumps(value, ensure_ascii=False), cid))
        return public(value)

    @router.get('/{cid}')
    def conversation(cid:str,request:Request):
        value=owned(cid,request);result=public(value);tasks=[]
        for kind in ('task','image_task','audio_task'):
            for task in h.listing(kind):
                if task.get('project_id')!=cid or task.get('deleted_at'):
                    continue
                if kind=='image_task':item=h.image_generation_status(task['id'])
                elif kind=='audio_task':item=h.audio_generation_status(task['id'])
                else:
                    if not task.get('local_stop_requested'):
                        task = h.refresh_video_task(task['id'])
                    item={k:task.get(k) for k in ('id','status','error','video_url','params','created_at','updated_at','provider_task_id','status_message','query_paused','auto_save_video','asset_save_status','asset_id','video_cache_status','video_cache_error')}
                item.update({key: task.get(key) for key in ('request_id', 'stop_requested', 'local_stop_requested', 'stopped_at', 'provider_may_continue')})
                item['request_id'] = task.get('request_id') or (task.get('params') or {}).get('request_id')
                settings = task.get('params') if kind == 'task' else {
                    key: task[key] for key in ('model_config_id', 'model', 'reference_asset_ids', 'size', 'aspect_ratio', 'count', 'watermark', 'mode', 'audio_config', 'speaker', 'visual_style', 'style_snapshot', 'effective_prompt') if key in task
                }
                item['generation_settings'] = {**(value.get('settings') or {}), **(settings or {})}
                if kind in ('task', 'image_task'):
                    # A turn with no style must not inherit the conversation's
                    # original style or its effective provider prompt.
                    for key in ('visual_style', 'style_snapshot', 'effective_prompt'):
                        item['generation_settings'][key] = (settings or {}).get(key)
                item['prompt'] = task.get('prompt') or (task.get('params') or {}).get('prompt') or value.get('prompt', '')
                tasks.append(item)
        result['tasks']=sorted(tasks,key=lambda t:t.get('created_at',0),reverse=True)
        return result

    @router.delete('/{cid}/tasks/{tid}')
    def delete_turn(cid: str, tid: str, request: Request):
        owned(cid, request)
        with h.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            records = [(kind, json.loads(raw)) for kind, raw in db.execute('SELECT kind,body FROM records')]
            value = next((v for k,v in records if k == 'conversation' and v['id'] == cid), None)
            if not value or value.get('deleted_at') or not authorized(value, request):
                raise HTTPException(404, '对话不存在')
            task_kind = {'image': 'image_task', 'video': 'task', 'audio': 'audio_task'}.get(value.get('kind'))
            task = next((v for k,v in records if k == task_kind and v['id'] == tid and v.get('project_id') == cid), None)
            if not task or task.get('deleted_at'):
                raise HTTPException(404, '生成记录不存在')
            h.require_access(task_kind, task)
            if task.get('status') in ('queued', 'running', 'waiting_tool'):
                raise HTTPException(409, '请等待本段生成完成后再删除')
            tasks = [v for k,v in records if k in ('task', 'image_task', 'audio_task') and not v.get('deleted_at')]
            remaining = [v for v in tasks if v.get('project_id') == cid and v['id'] != tid]
            all_assets = [v for k,v in records if k == 'asset']
            output_ids = {a['id'] for a in (task.get('assets') or []) if a.get('id')}
            if (task.get('asset') or {}).get('id'):
                output_ids.add(task['asset']['id'])
            outputs = [a for a in all_assets if not a.get('deleted_at') and (a.get('task_id') == tid or a['id'] in output_ids or (
                a.get('project_id') == cid and task.get('video_url') and a.get('url') == task['video_url']))]
            projects = [v for k,v in records if k == 'project']
            characters = [v for k,v in records if k == 'character']
            conversations = [v for k,v in records if k == 'conversation' and not v.get('deleted_at') and (v['id'] != cid or remaining)]
            for asset in outputs:
                h.require_access('asset', asset)
                usage = h.asset_public(asset, [p for p in projects if h.can_access('project', p)],
                    [c for c in characters if h.can_access('character', c)],
                    [a for a in all_assets if h.can_access('asset', a)],
                    [c for c in conversations if h.can_access('conversation', c)])
                if usage['usage_count']:
                    raise HTTPException(409, '生成素材已被画布、对话或其他素材引用，请先移除引用后再删除')
                for other in tasks:
                    if other['id'] == tid:
                        continue
                    params = other.get('params') or other
                    if asset['id'] in (params.get('reference_asset_ids') or params.get('asset_ids') or []):
                        raise HTTPException(409, '生成素材已被其他生成记录引用，请先移除引用后再删除')
            now = time.time()
            for record_kind, record in [(task_kind, task), *[('asset', a) for a in outputs]]:
                record.update(deleted_at=now, updated_at=now)
                db.execute('UPDATE records SET body=? WHERE kind=? AND id=?', (json.dumps(record, ensure_ascii=False), record_kind, record['id']))
            value.update(updated_at=now, revision=value.get('revision', 0) + 1)
            if not remaining:
                value['deleted_at'] = now
            db.execute("UPDATE records SET body=? WHERE kind='conversation' AND id=?", (json.dumps(value, ensure_ascii=False), cid))
        if task_kind in ('image_task', 'audio_task'):
            getattr(h, 'IMAGE_TASK_FALLBACKS' if task_kind == 'image_task' else 'AUDIO_TASK_FALLBACKS', {}).pop(tid, None)
        return {'deleted_task_id': tid, 'deleted_asset_ids': [a['id'] for a in outputs], 'conversation_deleted': not remaining}

    app.include_router(router)


def migrate_agent_conversations(h):
    """Move only untouched, automatically created Agent projects; preserve IDs/files."""
    changes=[]
    for run in h.listing('agent_run'):
        if run.get('creation_type') or not run.get('created_project'):
            continue
        project=h.raw_record('project',run.get('project_id',''))
        if not project or project.get('deleted_at') or project.get('groups'):
            continue
        if any(n.get('agent_run_id')!=run['id'] for n in project.get('nodes',[])):
            continue
        changes.append((run,project))
    if not changes:return
    from storage.backup import backup_database
    backup_database(h.connect, h.DATA, 'conversation-separation')
    with h.connect() as db:
        db.execute('BEGIN IMMEDIATE')
        for run,project in changes:
            conversation={**project,'creation_type':'conversation','kind':'agent','owner_id':run['owner_id'],'title':project['name'],'prompt':run.get('source',''),'run_id':run['id']}
            db.execute("UPDATE records SET kind='conversation',body=? WHERE kind='project' AND id=?",(json.dumps(conversation,ensure_ascii=False),project['id']))
            run={**run,'creation_type':'conversation','preferences':{**run.get('preferences',{}),'canvas':False}}
            db.execute("UPDATE records SET body=? WHERE kind='agent_run' AND id=?",(json.dumps(run,ensure_ascii=False),run['id']))

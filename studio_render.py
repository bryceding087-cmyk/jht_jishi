"""Persistent, user-triggered episode/final render jobs in the JHT process."""
from copy import deepcopy
import threading
import time
import uuid

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field

from creator_metadata import creator_scope
from studio_graph import digest, resolve_inputs, mark_downstream
from studio_render_adapter import render_node


class RenderRequest(BaseModel):
    model_config = ConfigDict(extra='forbid')
    project_id: str = Field(min_length=1, max_length=160)
    node_id: str = Field(min_length=1, max_length=160)
    revision: int = Field(ge=1)
    request_id: str = Field(min_length=1, max_length=160)


def render_signature(graph, node):
    inputs = {r['nodeId'] for r in node.get('mujian', {}).get('inputs', [])}
    return digest({'node': {k: node.get(k) for k in ('id', 'clips', 'aspect_ratio', 'assembly', 'mujian')},
                   'inputs': [{k: n.get(k) for k in ('id', 'assetId', 'mujian')} for n in graph['nodes'] if n['id'] in inputs],
                   'edges': [e for e in graph['edges'] if e['to'] == node['id']]})


class StudioRenderService:
    def __init__(self, host):
        self.h, self.lock = host, threading.RLock()
        self.slots = threading.Semaphore(1)
        for job in host.listing('studio_render'):
            if job['status'] in ('queued', 'running'):
                host.save('studio_render', {**job, 'status': 'interrupted', 'error': '服务已重启。已完成素材仍保留，可重新合成。'})

    def public(self, job):
        return {k: v for k, v in job.items() if k in ('id', 'project_id', 'node_id', 'status', 'progress', 'error', 'asset', 'bound', 'created_at', 'updated_at')}

    def patch(self, tid, **fields):
        with self.lock:
            return self.h.save('studio_render', {**self.h.get('studio_render', tid), **fields, 'updated_at': time.time()})

    def create(self, body, owner):
        tid = 'render-' + str(uuid.uuid5(uuid.NAMESPACE_URL, f'{owner}:{body.request_id}'))
        with self.lock:
            previous = self.h.raw_record('studio_render', tid)
            if previous:
                if previous['digest'] != digest(body.model_dump()): raise HTTPException(409, '合成请求编号已用于其他内容')
                return self.public(previous)
            graph = self.h.active_project(body.project_id)
            if graph['revision'] != body.revision: raise HTTPException(409, '画布已更新，请重新读取后合成')
            node = next((n for n in graph['nodes'] if n['id'] == body.node_id and n['type'] == 'timeline'), None)
            if not node: raise HTTPException(404, '合成节点不存在')
            resolve_inputs(self.h, graph, node)
            if any(j['project_id'] == body.project_id and j['node_id'] == body.node_id and j['status'] in ('queued', 'running') for j in self.h.listing('studio_render')):
                raise HTTPException(409, '这个节点正在合成，请等待完成')
            job = self.h.save('studio_render', {'id': tid, 'project_id': body.project_id, 'node_id': body.node_id,
                'owner_id': str(owner), 'digest': digest(body.model_dump()), 'signature': render_signature(graph, node),
                'revision': body.revision, 'status': 'queued', 'progress': '排队合成', 'created_at': time.time(), 'updated_at': time.time()})
        threading.Thread(target=self.work, args=(tid,), daemon=True).start()
        return self.public(job)

    def work(self, tid):
        job = self.h.get('studio_render', tid)
        with self.h.worker_scope(job), creator_scope(self.h.record_creator(job, legacy_owner=True)), self.slots:
            try:
                if self.h.get('studio_render', tid).get('cancel_requested'): return
                graph = self.h.active_project(job['project_id'])
                node = next((n for n in graph['nodes'] if n['id'] == job['node_id']), None)
                if not node or render_signature(graph, node) != job['signature']: raise HTTPException(409, '排队期间剪辑输入已修改，请重新提交')
                self.patch(tid, status='running')
                asset = render_node(self.h, job['project_id'], job['node_id'], graph['revision'],
                    progress=lambda text: self.patch(tid, progress=text),
                    cancelled=lambda: self.h.get('studio_render', tid).get('cancel_requested', False))
                self.patch(tid, asset=asset)
                bound = False
                for _ in range(3):
                    graph = deepcopy(self.h.active_project(job['project_id']))
                    node = next((n for n in graph['nodes'] if n['id'] == job['node_id']), None)
                    if not node or render_signature(graph, node) != job['signature']: break
                    if node.get('assetId') != asset['id']: mark_downstream(graph, node['id'])
                    node.update(assetId=asset['id'], assetUrl=asset['url'])
                    try:
                        self.h.update_project(job['project_id'], self.h.Project(**graph)); bound = True; break
                    except HTTPException as error:
                        if error.status_code != 409: raise
                self.patch(tid, status='succeeded', bound=bound, progress='合成完成' if bound else '合成完成，画布有修改，成品已保留在生成历史')
            except Exception as error:
                cancelled = self.h.get('studio_render', tid).get('cancel_requested')
                self.patch(tid, status='cancelled' if cancelled else 'failed',
                           error='合成已停止' if cancelled else str(error.detail) if isinstance(error, HTTPException) else self.h.provider_error_message(error))


def install_studio_render(app, host):
    service = StudioRenderService(host)
    router = APIRouter(prefix='/api/studio/renders')

    @router.post('', status_code=202)
    def create(body: RenderRequest, request: Request):
        return service.create(body, request.state.user['id'])

    @router.get('')
    def listing(project_id: str):
        host.active_project(project_id)
        return [service.public(j) for j in host.listing('studio_render') if j['project_id'] == project_id]

    @router.get('/{tid}')
    def get(tid: str):
        return service.public(host.get('studio_render', tid))

    @router.post('/{tid}/cancel')
    def cancel(tid: str):
        job = host.get('studio_render', tid)
        if job['status'] not in ('queued', 'running'): raise HTTPException(409, '合成任务已经结束')
        return service.public(service.patch(tid, cancel_requested=True, **({'status': 'cancelled'} if job['status'] == 'queued' else {})))

    app.include_router(router)
    app.state.studio_render = service

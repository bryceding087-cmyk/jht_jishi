"""Provider presets and video wire formats, independent of application storage."""

from copy import deepcopy
from urllib.parse import quote, urlparse
from content_errors import normalize_content_error


PROVIDERS = {
    'text': [{
        'id': 'openai-compatible', 'name': 'OpenAI 兼容接口',
        'description': '使用 Chat Completions 接口，可填写兼容服务商的地址。',
        'defaults': {'model_id': 'qwen-plus', 'base_url': 'https://dashscope.aliyuncs.com/compatible-mode/v1',
                     'api_path': '/chat/completions'},
    }, {
        'id': 'aimarket', 'name': 'AI Market',
        'description': '使用 AI 集市的 Chat Completions 接口，集市入口使用当前会话凭据。',
        'defaults': {'model_id': 'deepseek-v4-flash-vision-exp-0817',
                     'base_url': 'http://192.168.241.202:4091', 'api_path': '/v1/chat/completions'},
    }],
    'image': [{
        'id': 'ark', 'name': '火山方舟 Ark',
        'description': '使用火山方舟 Images Generations 兼容接口。',
        'defaults': {'model_id': 'doubao-seedream-5-0-pro-260628',
                     'base_url': 'https://ark.cn-beijing.volces.com/api/v3', 'api_path': '/images/generations'},
    }, {
        'id': 'aimarket', 'name': 'AI Market',
        'description': '使用 AI 集市的 Images Generations 接口。',
        'defaults': {'model_id': 'doubao-seedream-5.0-pro',
                     'base_url': 'http://192.168.241.202:4091', 'api_path': '/v1/images/generations'},
    }],
    'audio': [{
        'id': 'volcengine-speech', 'name': '火山引擎语音',
        'description': 'Seed Audio 非流式音频生成，使用 X-Api-Key 鉴权。支持文本、音频或图片参考，最长生成 120 秒音频。',
        'defaults': {'model_id': 'seed-audio-1.0', 'base_url': 'https://openspeech.bytedance.com',
                     'api_path': '/api/v3/tts/create'},
    }],
    'video': [{
        'id': 'ark', 'name': '火山方舟 Ark',
        'description': '按火山方舟协议提交视频任务，并查询任务状态。',
        'defaults': {'model_id': 'doubao-seedance-2-0-mini-260615',
                     'base_url': 'https://ark.cn-beijing.volces.com',
                     'api_path': '/api/v3/contents/generations/tasks',
                     'query_path': '/api/v3/contents/generations/tasks/{task_id}'},
    }, {
        'id': 'aimarket', 'name': 'AI Market',
        'description': '使用独立的视频提交与查询接口。minimax-h3 支持 768P、2K 和 4～15 秒；参考图支持本地图片数据或公网 URL。',
        'defaults': {'model_id': 'minimax-h3', 'base_url': 'http://192.168.241.202:4091',
                     'api_path': '/v1/video_generation',
                     'query_path': '/v1/query/video_generation/{task_id}'},
    }],
}


def provider_catalog():
    return deepcopy(PROVIDERS)


def provider_id(kind, config):
    selected = config.get('provider_id')
    if selected:
        if not any(item['id'] == selected for item in PROVIDERS[kind]):
            raise ValueError('所选服务商不支持此模型类型')
        return selected
    # Old records only stored a display name. Preserve their original protocol,
    # while recognising saved configurations for public and internal Market hosts.
    if kind in ('text', 'image', 'video') and (str(config.get('provider', '')).lower() in ('aimarket', 'ai market')
                            or urlparse(config.get('base_url', '')).hostname in ('aimarket.hncig.cn', '192.168.241.202')):
        return 'aimarket'
    return PROVIDERS[kind][0]['id']


def provider_preset(kind, config):
    selected = provider_id(kind, config)
    return next(item for item in PROVIDERS[kind] if item['id'] == selected)


def video_capabilities(config):
    capabilities = deepcopy(config.get('capabilities') or {})
    if provider_id('video', config) == 'ark' and any(name in config.get('model_id', '') for name in ('seedance-2-0', 'seedance-2.0')):
        capabilities.update({'durations': list(range(4, 16)), 'aspect_ratios': ['16:9', '9:16', '1:1', '4:3', '3:4', '21:9']})
    if provider_id('video', config) == 'aimarket' and config.get('model_id') == 'minimax-h3':
        # AI Market's published H3 profile lists 768P and 2K; its API validates
        # a 4–15 second duration. Apply this on read so saved configurations
        # also stop inheriting Seedance's 720p option. Other model IDs have
        # their own contracts and must not inherit the H3 limits.
        capabilities.update({'resolutions': ['768p', '2k'], 'durations': list(range(4, 16))})
    return capabilities


def video_reference_transport(config):
    # H3 V2 accepts inline Base64 image data. Keep other gateway models on
    # their existing URL contract rather than inferring support from H3.
    return 'data_uri' if (provider_id('video', config) != 'aimarket'
                          or config.get('model_id') == 'minimax-h3') else 'public_http_url'


def video_query_path(config):
    if config.get('query_path'):
        return config['query_path']
    if provider_id('video', config) == 'aimarket':
        return provider_preset('video', config)['defaults']['query_path']
    return config.get('api_path', PROVIDERS['video'][0]['defaults']['api_path']).rstrip('/') + '/{task_id}'


def video_query_url(config, task_id):
    path = video_query_path(config).replace('{task_id}', quote(str(task_id), safe=''))
    return config['base_url'].rstrip('/') + '/' + path.lstrip('/')


def video_payload(params, config, reference_url, reference_kind=lambda _: 'image'):
    prompt = params.get('effective_prompt') or params['prompt']
    if params.get('negative_prompt'):
        prompt += '\n\nNegative prompt: ' + params['negative_prompt']
    content = [{'type': 'text', 'text': prompt}]
    for index, asset_id in enumerate(params.get('asset_ids', [])):
        if reference_kind(asset_id) == 'video':
            content.append({'type': 'video_url', 'video_url': {'url': reference_url(asset_id)}, 'role': 'reference_video'})
            continue
        if reference_kind(asset_id) == 'audio':
            content.append({'type': 'audio_url', 'audio_url': {'url': reference_url(asset_id)}, 'role': 'reference_audio'})
            continue
        item = {'type': 'image_url', 'image_url': {'url': reference_url(asset_id)}}
        if params.get('mode') == 'first-last':
            item['role'] = 'first_frame' if index == 0 else 'last_frame'
        elif params.get('mode') in ('image-to-video', 'multi-frame'):
            item['role'] = 'reference_image'
        content.append(item)
    payload = {'model': config['model_id'], 'content': content,
               'resolution': params['resolution'], 'duration': params['duration'], 'ratio': params['aspect_ratio']}
    if provider_id('video', config) == 'aimarket':
        payload['resolution'] = payload['resolution'].upper()
        # The documented AI Market request omits watermark. Both false and
        # "false" are rejected by H3; do not forward Ark-only flags here.
    else:
        payload['watermark'] = False
        payload['generate_audio'] = True
    return payload


# Restrict traversal to response envelopes, so echoed input images and request
# metadata cannot be mistaken for a completed video or an upstream task ID.
ENVELOPES = ('data', 'output', 'result', 'task', 'response', 'raw_response', 'raw')
STATES = {
    'queued': 'queued', 'in_queue': 'queued', 'pending': 'queued', 'waiting': 'queued', 'created': 'queued',
    'preparing': 'running', 'running': 'running', 'generating': 'running',
    'processing': 'running', 'in_progress': 'running', 'inprogress': 'running',
    'succeeded': 'done', 'success': 'done', 'done': 'done', 'completed': 'done', 'finished': 'done',
    'failed': 'failed', 'fail': 'failed', 'error': 'failed', 'expired': 'failed',
    'cancelled': 'failed', 'canceled': 'failed',
}


def response_objects(value):
    if isinstance(value, dict):
        yield value
        for key in ENVELOPES:
            yield from response_objects(value.get(key))
    elif isinstance(value, list):
        for item in value:
            yield from response_objects(item)


def video_task_id(response):
    objects = list(response_objects(response))
    for key in ('task_id', 'id'):
        for item in objects:
            value = item.get(key)
            if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip():
                return str(value)
    return ''


def video_response_error(response):
    for item in response_objects(response):
        if item.get('error'):
            return normalize_content_error(item['error'])
        base = item.get('base_resp')
        if isinstance(base, dict) and str(base.get('status_code', 0)) != '0':
            normalized = normalize_content_error(base)
            if isinstance(normalized, dict):
                return normalized.get('status_msg') or normalized
            return normalized
        code = item.get('code')
        if item.get('success') is False or (isinstance(code, (int, str)) and str(code).isdigit() and str(code) not in ('0', '200')):
            normalized = normalize_content_error(item)
            if normalized != item:
                return normalized
            return item.get('message') or item.get('msg') or f'模型接口返回错误码：{code}'
    return None


def video_result_url(response):
    def http_url(value):
        return value if isinstance(value, str) and value.startswith(('http://', 'https://')) else ''

    def media_url(value, generic=False):
        if isinstance(value, str):
            return http_url(value) if generic else ''
        if isinstance(value, list):
            return next((found for item in value if (found := media_url(item, generic))), '')
        if not isinstance(value, dict) or value.get('type') in ('image_url', 'image', 'text'):
            return ''
        for key in ('video_url', 'download_url', 'file_url'):
            found = media_url(value.get(key), True)
            if found:
                return found
        for key in ('content', 'video', 'videos', 'files', *ENVELOPES):
            found = media_url(value.get(key), True)
            if found:
                return found
        return http_url(value.get('url')) if generic else ''

    return media_url(response, True)


def video_result(response):
    error = video_response_error(response)
    objects = list(response_objects(response))
    status = ''
    # Native task states take precedence over a gateway's outer "success" flag.
    for item in reversed(objects):
        for key in ('task_status', 'status', 'state'):
            raw = str(item.get(key, '')).strip().lower().replace('-', '_').replace(' ', '_')
            if raw in STATES:
                status = STATES[raw]
                break
        if status:
            break
    if error:
        status = 'failed'
    if status == 'failed' and not error:
        error = next((item.get('message') or item.get('msg') for item in reversed(objects)
                      if item.get('message') or item.get('msg')), '模型任务失败')
    if not status:
        raise ValueError('模型接口返回了无法识别的任务状态，请核对服务商的查询响应')
    return {'status': status, 'video_url': video_result_url(response) if status == 'done' else '', 'error': normalize_content_error(error)}

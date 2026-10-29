"""Server-provisioned video models, visible only to their assigned account."""
from copy import deepcopy
import json
from pathlib import Path
import re

from fastapi import HTTPException
from data_access import current_principal


ID_PREFIX = 'account-video:'
FILE_NAME = 'account-video-models.json'
PROVIDERS = {
    'minimax': ('MiniMax 官网', 'https://api.minimax.cn', '/v2/video_generation',
                '/v2/query/video_generation/{task_id}'),
    'ark': ('火山方舟 Ark', 'https://ark.cn-beijing.volces.com',
            '/api/v3/contents/generations/tasks', '/api/v3/contents/generations/tasks/{task_id}'),
}


def is_account_video(config):
    return bool(config.get('_account_username'))


def require_account_video_access(config):
    user = current_principal()
    if (not user or user.get('active') is False or
            user.get('username') != config.get('_account_username')):
        raise HTTPException(404, '模型配置不存在')
    preset = PROVIDERS.get(config.get('provider_id'))
    if not preset or tuple(config.get(key) for key in ('base_url', 'api_path', 'query_path')) != preset[1:]:
        raise HTTPException(422, '账号专属模型的服务地址配置无效')
    return config


def validate_document(document):
    """Validate installation input without ever including credentials in errors."""
    try:
        if not isinstance(document, dict) or document.get('version') != 1:
            raise ValueError
        accounts = document['accounts']
        if not isinstance(accounts, list):
            raise ValueError
        seen_users, seen_models = set(), set()
        for account in accounts:
            username = account['username']
            if not isinstance(username, str) or not username or len(username) > 160 or username in seen_users:
                raise ValueError
            seen_users.add(username)
            if not isinstance(account['models'], list) or not account['models']:
                raise ValueError
            for model in account['models']:
                mid = model['id']
                if not isinstance(mid, str) or not re.fullmatch(r'account-video:[a-z0-9-]{1,100}', mid) or mid in seen_models:
                    raise ValueError
                seen_models.add(mid)
                preset = PROVIDERS[model['provider_id']]
                if tuple(model.get(k) for k in ('base_url', 'api_path', 'query_path')) != preset[1:]:
                    raise ValueError
                if not all(isinstance(model.get(k), str) and model[k].strip() for k in ('name', 'model_id', 'api_key')):
                    raise ValueError
                if len(model['api_key']) > 500 or any(c.isspace() for c in model['api_key']):
                    raise ValueError
                if not isinstance(model.get('capabilities', {}), dict):
                    raise ValueError
        return document
    except (KeyError, TypeError, ValueError):
        raise ValueError('账号专属模型配置文件格式无效') from None


class AccountVideoModels:
    def __init__(self, data):
        self.path = Path(data) / FILE_NAME

    def models(self):
        user = current_principal()
        if not user or user.get('active') is False or not self.path.is_file():
            return []
        try:
            document = validate_document(json.loads(self.path.read_text(encoding='utf-8')))
        except (OSError, ValueError):
            raise HTTPException(503, '账号专属模型配置暂不可用，请联系管理员') from None
        account = next((a for a in document['accounts'] if a['username'] == user.get('username')), None)
        if not account:
            return []
        return [{**deepcopy(model), 'provider': PROVIDERS[model['provider_id']][0],
                 '_account_username': account['username']} for model in account['models']]

    def get(self, mid):
        model = next((model for model in self.models() if model['id'] == mid), None)
        if model is None:
            raise HTTPException(404, '模型配置不存在')
        return require_account_video_access(model)

    def can_select(self, kind):
        return kind == 'video' and bool(self.models())

    @staticmethod
    def preference_id(kind):
        user = current_principal()
        return f"account-model-default:{user['id']}:{kind}" if user else ''

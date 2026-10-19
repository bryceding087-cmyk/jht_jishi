"""Deployment-specific AI Market endpoints, independent of model credentials."""
import os
from urllib.parse import urlsplit


LOCAL_MODEL_ORIGIN = 'https://aimarket.hncig.cn:4090'
DOCKER_MODEL_ORIGIN = 'http://192.168.241.202:4091'
MODEL_ORIGIN_ENV = 'JIANHUITONG_MARKET_MODEL_ORIGIN'


def model_gateway_origin():
    # The launchers select the environment; isolated imports keep the server default.
    return os.getenv(MODEL_ORIGIN_ENV, '').strip().rstrip('/') or DOCKER_MODEL_ORIGIN


def with_model_gateway(config):
    """Resolve saved standard gateway addresses without changing custom providers."""
    result = dict(config)
    if config.get('provider_id') != 'aimarket':
        return result
    try:
        url = urlsplit(str(config.get('base_url') or ''))
        origin = f'{url.scheme}://{url.netloc.lower()}'
        if (origin in (LOCAL_MODEL_ORIGIN, DOCKER_MODEL_ORIGIN)
                and not (url.username or url.password or url.query or url.fragment)):
            result['base_url'] = model_gateway_origin() + url.path.rstrip('/')
    except ValueError:
        pass
    return result

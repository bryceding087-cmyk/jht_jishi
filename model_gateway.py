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


def video_download_url(value, target_origin):
    """Use this deployment's gateway for AI Market's own video files.

    Only the known video asset route is translated. CDN URLs and their
    signatures remain untouched, as do the path and query of gateway URLs.
    """
    url = str(value)
    if not target_origin:
        return url
    try:
        parsed = urlsplit(url)
        source_origin = f'{parsed.scheme}://{parsed.netloc.lower()}'
        if (source_origin not in (LOCAL_MODEL_ORIGIN, DOCKER_MODEL_ORIGIN)
                or parsed.username or parsed.password
                or not parsed.path.startswith('/v1/video-assets/')):
            return url
        target = urlsplit(str(target_origin))
        return parsed._replace(scheme=target.scheme, netloc=target.netloc).geturl()
    except ValueError:
        return url

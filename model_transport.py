"""Scoped DNS recovery before sending a model request, retaining Host/TLS SNI."""
import http.client
import ipaddress
import json
import socket
import threading
import time
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPHandler, HTTPSHandler, ProxyHandler, Request, build_opener, urlopen as dns_urlopen


DNS_FALLBACK_HOSTS = frozenset({'maas.qianwenaiapi.com'})
_dns_cache = {}
_dns_lock = threading.Lock()


def fallback_addresses(host):
    """Use authenticated Alibaba DNS only for the known public Qwen host."""
    host = host.lower().rstrip('.')
    if host not in DNS_FALLBACK_HOSTS:
        raise socket.gaierror(socket.EAI_NONAME, 'No DNS fallback configured')
    with _dns_lock:
        cached = _dns_cache.get(host)
        if cached and cached[0] > time.monotonic(): return cached[1]
    request = Request('https://dns.alidns.com/resolve?' + urlencode({'name': host, 'type': 'A'}),
                      headers={'Accept': 'application/dns-json'})
    with dns_urlopen(request, timeout=8) as response:
        raw = response.read(65537)
    if len(raw) > 65536: raise ValueError('DNS answer too large')
    body = json.loads(raw)
    if body.get('Status') != 0: raise socket.gaierror(socket.EAI_NONAME, 'DNS lookup failed')
    answers = body.get('Answer', [])
    names, ttl = {host}, 300
    for _ in range(8):
        for answer in answers:
            if str(answer.get('name', '')).lower().rstrip('.') in names and answer.get('type') == 5:
                names.add(str(answer['data']).lower().rstrip('.'))
                ttl = min(ttl, int(answer.get('TTL', 0)))
    addresses = []
    for answer in answers:
        if answer.get('type') != 1 or str(answer.get('name', '')).lower().rstrip('.') not in names: continue
        ip = ipaddress.ip_address(answer['data'])
        if ip.version != 4 or not ip.is_global: continue
        if str(ip) not in addresses: addresses.append(str(ip))
        ttl = min(ttl, int(answer.get('TTL', 0)))
    if not addresses: raise socket.gaierror(socket.EAI_NONAME, 'No public address found')
    with _dns_lock: _dns_cache[host] = (time.monotonic() + max(0, ttl), addresses)
    return addresses


def create_model_connection(address, timeout=socket._GLOBAL_DEFAULT_TIMEOUT, source_address=None, **kwargs):
    with _dns_lock:
        cached = _dns_cache.get(address[0].lower().rstrip('.'))
        addresses = cached[1] if cached and cached[0] > time.monotonic() else None
    if addresses:
        # The successful fallback remains valid only for its short DNS TTL.
        last_error = None
        for ip in addresses:
            try: return socket.create_connection((ip, address[1]), timeout, source_address, **kwargs)
            except OSError as error: last_error = error
        raise last_error
    try:
        return socket.create_connection(address, timeout, source_address, **kwargs)
    except socket.gaierror as original:
        # Only connect() is retried. No HTTP headers or body have been sent yet.
        host, port = address
        if host.lower().rstrip('.') not in DNS_FALLBACK_HOSTS: raise
        try:
            addresses = fallback_addresses(host)
        except Exception:
            raise original from None
        last_error = original
        for ip in addresses:
            try: return socket.create_connection((ip, port), timeout, source_address, **kwargs)
            except OSError as error: last_error = error
        raise last_error from None


class ModelHTTPConnection(http.client.HTTPConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = create_model_connection


class ModelHTTPSConnection(http.client.HTTPSConnection):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._create_connection = create_model_connection


class ModelHTTPHandler(HTTPHandler):
    def http_open(self, request):
        return self.do_open(ModelHTTPConnection, request)


class ModelHTTPSHandler(HTTPSHandler):
    def https_open(self, request):
        return self.do_open(ModelHTTPSConnection, request, context=self._context)


def model_http_handlers():
    return [ModelHTTPHandler(), ModelHTTPSHandler()]


def model_urlopen(request, *, timeout):
    handlers = model_http_handlers()
    url = urlsplit(request.full_url if isinstance(request, Request) else request)
    host = (url.hostname or '').lower().rstrip('.')
    if host in DNS_FALLBACK_HOSTS:
        with _dns_lock:
            cached = _dns_cache.get(host)
            recovered = bool(cached and cached[0] > time.monotonic())
        if not recovered:
            try: socket.getaddrinfo(host, url.port or 443, type=socket.SOCK_STREAM)
            except socket.gaierror:
                try:
                    fallback_addresses(host)
                    recovered = True
                except Exception: pass
        if recovered:
            # The local proxy may have the same broken DNS. Dial the verified
            # address directly while HTTPS still validates the original host.
            handlers.insert(0, ProxyHandler({}))
    return build_opener(*handlers).open(request, timeout=timeout)

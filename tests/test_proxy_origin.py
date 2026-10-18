"""Browser origin validation through HTTPS termination and Host rewriting."""
import unittest
from starlette.requests import Request
from auth_service import same_origin_request

PUBLIC = 'https://aimarket.hncig.cn:4193'

def request(origin=PUBLIC, host='aimarket.hncig.cn:4193', metadata='same-origin', **extra):
    headers = {'host': host, 'origin': origin, 'sec-fetch-site': metadata, **extra}
    return Request({'type': 'http', 'scheme': 'http', 'method': 'POST',
        'path': '/api/integrations/aimarket/login', 'root_path': '', 'query_string': b'',
        'server': ('192.168.168.117', 8000),
        'headers': [(k.encode(), v.encode()) for k, v in headers.items()]})

class ProxyOriginTests(unittest.TestCase):
    def test_reproduces_original_rejection_then_accepts_configured_public_origin(self):
        for host in ('aimarket.hncig.cn:4193', '192.168.168.117:8000'):
            with self.subTest(host=host):
                req = request(host=host)
                self.assertFalse(same_origin_request(req))
                self.assertTrue(same_origin_request(req, PUBLIC))

    def test_exact_protocol_host_port_and_browser_metadata_required(self):
        for origin in ('http://aimarket.hncig.cn:4193', 'https://aimarket.hncig.cn',
                       'https://aimarket.hncig.cn:4090', 'https://aimarket.hncig.cn.evil:4193',
                       'https://evil.test', 'null', PUBLIC + '/market', PUBLIC + '?x=1'):
            with self.subTest(origin=origin):
                self.assertFalse(same_origin_request(request(origin=origin, host='192.168.168.117:8000'), PUBLIC))
        for metadata in ('', 'same-site', 'cross-site', 'none'):
            self.assertFalse(same_origin_request(request(metadata=metadata), PUBLIC))

    def test_forwarded_headers_cannot_expand_allowlist(self):
        self.assertFalse(same_origin_request(request(origin='https://evil.test', **{
            'x-forwarded-proto': 'https', 'x-forwarded-host': 'evil.test',
            'forwarded': 'proto=https;host=evil.test'}), PUBLIC))

    def test_direct_internal_origin_still_works(self):
        self.assertTrue(same_origin_request(request(origin='http://192.168.168.117:8000',
            host='192.168.168.117:8000'), PUBLIC))

if __name__ == '__main__':
    unittest.main()

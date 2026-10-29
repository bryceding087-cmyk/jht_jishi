import io
import json
import socket
import unittest
from unittest.mock import patch, Mock
from urllib.request import Request, ProxyHandler

import model_transport as transport


HOST = 'maas.qianwenaiapi.com'


class ModelTransportTests(unittest.TestCase):
    def setUp(self):
        transport._dns_cache.clear()

    def answer(self, ip='101.201.58.201'):
        return io.BytesIO(json.dumps({'Status': 0, 'Answer': [
            {'name': HOST+'.', 'type': 5, 'data': 'model.aliyuncsslb.com.', 'TTL': 300},
            {'name': 'model.aliyuncsslb.com.', 'type': 1, 'data': ip, 'TTL': 60}]}).encode())

    def test_healthy_connection_does_not_use_dns_service(self):
        with patch.object(transport.socket, 'create_connection', return_value='connected') as connect, patch.object(transport, 'fallback_addresses') as resolve:
            self.assertEqual(transport.create_model_connection((HOST, 443), 12), 'connected')
            connect.assert_called_once(); resolve.assert_not_called()

    def test_dns_failure_is_recovered_before_request_transmission(self):
        with patch.object(transport.socket, 'create_connection', side_effect=[socket.gaierror(11001, 'DNS failure'), 'connected']) as connect, patch.object(transport, 'dns_urlopen', return_value=self.answer()) as dns:
            self.assertEqual(transport.create_model_connection((HOST, 443), 12), 'connected')
            self.assertEqual(connect.call_args_list[1].args[0], ('101.201.58.201',443))
            self.assertEqual(dns.call_args.args[0].get_header('Authorization'), None)
            self.assertEqual(transport.fallback_addresses(HOST), ['101.201.58.201'])
            dns.assert_called_once()

    def test_unrelated_host_and_timeouts_do_not_trigger_fallback(self):
        for host, error in [('private.internal',socket.gaierror(11001,'DNS failure')),(HOST,TimeoutError('connect timed out'))]:
            with self.subTest(host=host), patch.object(transport.socket, 'create_connection', side_effect=error) as connect, patch.object(transport, 'dns_urlopen') as dns:
                with self.assertRaises(type(error)):transport.create_model_connection((host,443),12)
                dns.assert_not_called();connect.assert_called_once()

    def test_dns_answers_must_be_public_and_expire(self):
        with patch.object(transport,'dns_urlopen',return_value=self.answer('127.0.0.1')):
            with self.assertRaises(socket.gaierror):transport.fallback_addresses(HOST)
        with patch.object(transport,'dns_urlopen',side_effect=[self.answer(),self.answer()]) as dns, patch.object(transport.time,'monotonic',return_value=100):
            transport.fallback_addresses(HOST)
            self.assertEqual(transport._dns_cache[HOST][0],160)
            with patch.object(transport.time,'monotonic',return_value=161):transport.fallback_addresses(HOST)
            self.assertEqual(dns.call_count,2)

    def test_fallback_does_not_replace_http_host_or_tls_name(self):
        tls=Mock();connection=transport.ModelHTTPSConnection(HOST,context=tls)
        connection._create_connection=Mock(return_value=Mock())
        connection.connect()
        self.assertEqual(connection.host,HOST)
        self.assertEqual(tls.wrap_socket.call_args.kwargs['server_hostname'],HOST)

    def test_dns_recovery_uses_direct_connection_with_original_request(self):
        request=Request('https://'+HOST+'/compatible-mode/v1/responses',data=b'{}',headers={'Authorization':'Bearer fixture'})
        opener=Mock();opener.open.return_value=io.BytesIO(b'{}')
        with patch.object(transport.socket,'getaddrinfo',side_effect=socket.gaierror(11001,'DNS failure')), patch.object(transport,'dns_urlopen',return_value=self.answer()),patch.object(transport,'build_opener',return_value=opener) as factory:
            transport.model_urlopen(request,timeout=20)
        handlers=factory.call_args.args
        self.assertIsInstance(handlers[0],ProxyHandler)
        self.assertEqual(handlers[0].proxies,{})
        opener.open.assert_called_once_with(request,timeout=20)
        self.assertEqual(request.host,HOST)


if __name__ == '__main__': unittest.main()

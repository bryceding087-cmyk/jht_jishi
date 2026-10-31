"""Download origin checks allow only the configured internal model gateway."""
import unittest
from unittest.mock import patch
from urllib.request import Request

from video_service import validate_video_url, PublicVideoRedirectHandler, download_generated_video


class VideoDownloadTests(unittest.TestCase):
    origin = 'http://192.168.241.202:4091'

    def test_server_downloads_gateway_video_via_internal_origin_and_preserves_signature(self):
        path = '/v1/video-assets/result.mp4?token=a%2Fb%2Bc&expires=999&part=1&part=2'
        with patch('video_service.build_opener') as opener, patch('video_service.socket.getaddrinfo', side_effect=AssertionError('no public DNS needed')):
            response = opener.return_value.open.return_value.__enter__.return_value
            response.geturl.return_value = self.origin + path
            response.headers = {'Content-Type': 'video/mp4'}
            response.read.side_effect = [b'fixture-video', b'']
            self.assertEqual(download_generated_video('https://aimarket.hncig.cn:4090' + path, self.origin), (b'fixture-video', '.mp4'))
            self.assertEqual(opener.return_value.open.call_args.args[0].full_url, self.origin + path)

    def test_local_video_downloads_use_public_gateway_and_cdn_urls_stay_unchanged(self):
        from model_gateway import video_download_url
        local = 'https://aimarket.hncig.cn:4090'
        path = '/v1/video-assets/result.mp4?signature=keep%2Fthis'
        self.assertEqual(video_download_url(self.origin + path, local), local + path)
        for url in ('https://cdn.test' + path, local + '/unrelated/file.mp4',
                    'https://aimarket.hncig.cn.evil.test:4090' + path,
                    'https://user@aimarket.hncig.cn:4090' + path):
            self.assertEqual(video_download_url(url, self.origin), url)

    def test_gateway_video_redirect_also_uses_internal_route(self):
        handler = PublicVideoRedirectHandler(self.origin)
        path = '/v1/video-assets/redirected.mp4?token=a%2Fb'
        with patch('video_service.socket.getaddrinfo', side_effect=AssertionError('no public DNS needed')):
            request = handler.redirect_request(Request(self.origin + '/file.mp4'), None, 302, '', {},
                'https://aimarket.hncig.cn:4090' + path)
            self.assertEqual(request.full_url, self.origin + path)

    def test_exact_gateway_origin_is_allowed_without_public_dns(self):
        with patch('video_service.socket.getaddrinfo', side_effect=AssertionError('no DNS needed')):
            self.assertEqual(validate_video_url(self.origin + '/generated/file.mp4', self.origin), self.origin + '/generated/file.mp4')

    def test_private_targets_and_near_matching_origins_are_rejected(self):
        with patch('video_service.socket.getaddrinfo', return_value=[(0, 0, 0, '', ('127.0.0.1', 443))]):
            for url in ['http://192.168.241.202:4092/file.mp4', 'http://192.168.241.201:4091/file.mp4',
                        'https://192.168.241.202:4091/file.mp4', 'https://localhost/file.mp4',
                        'http://user:pass@192.168.241.202:4091/file.mp4', 'file:///video.mp4']:
                with self.subTest(url=url), self.assertRaises(ValueError):
                    validate_video_url(url, self.origin)

    def test_gateway_redirect_to_other_private_target_is_rejected(self):
        handler = PublicVideoRedirectHandler(self.origin)
        with patch('video_service.socket.getaddrinfo', return_value=[(0, 0, 0, '', ('127.0.0.1', 443))]):
            with self.assertRaises(ValueError):
                handler.redirect_request(Request(self.origin + '/file.mp4'), None, 302, '', {}, 'https://localhost/file.mp4')
        with patch('video_service.socket.getaddrinfo', return_value=[(0, 0, 0, '', ('8.8.8.8', 443))]):
            target = handler.redirect_request(Request(self.origin + '/file.mp4'), None, 302, '', {}, 'https://cdn.test/file.mp4')
            self.assertEqual(target.full_url, 'https://cdn.test/file.mp4')

    def test_download_validates_final_url_and_returns_bytes(self):
        with patch('video_service.build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.geturl.return_value = self.origin + '/file.mp4'
            response.headers = {'Content-Type': 'video/mp4'}
            response.read.side_effect = [b'fixture-video', b'']
            self.assertEqual(download_generated_video(self.origin + '/file.mp4', self.origin), (b'fixture-video', '.mp4'))
            response.geturl.return_value = 'http://127.0.0.1/private.mp4'
            with self.assertRaises(ValueError):
                download_generated_video(self.origin + '/file.mp4', self.origin)

    def test_short_response_is_never_marked_as_a_saved_video(self):
        with patch('video_service.build_opener') as opener:
            response = opener.return_value.open.return_value.__enter__.return_value
            response.geturl.return_value = self.origin + '/file.mp4'
            response.headers = {'Content-Type': 'video/mp4', 'Content-Length': '100'}
            response.read.side_effect = [b'only part of the video', b'']
            with self.assertRaises(ConnectionError):
                download_generated_video(self.origin + '/file.mp4', self.origin)


if __name__ == '__main__':
    unittest.main()

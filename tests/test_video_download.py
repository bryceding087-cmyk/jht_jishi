"""Download origin checks allow only the configured internal model gateway."""
import unittest
from unittest.mock import patch
from urllib.request import Request

from video_service import validate_video_url, PublicVideoRedirectHandler, download_generated_video


class VideoDownloadTests(unittest.TestCase):
    origin = 'http://192.168.241.202:4091'

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


if __name__ == '__main__':
    unittest.main()

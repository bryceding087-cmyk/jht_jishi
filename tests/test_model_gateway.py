"""Local/container launch configuration without real databases or upstream calls."""
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from model_gateway import DOCKER_MODEL_ORIGIN, LOCAL_MODEL_ORIGIN, MODEL_ORIGIN_ENV, with_model_gateway
from server import load_deployment_config


class GatewayConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix='jht-gateway-')
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database = 'JIANHUITONG_DATABASE_URL=postgresql+psycopg://fixture:fixture@127.0.0.1:55432/fixture\n'

    def test_launch_defaults_match_local_and_container_environments(self):
        (self.root / '.env').write_text(self.database, encoding='utf-8')
        for default in (LOCAL_MODEL_ORIGIN, DOCKER_MODEL_ORIGIN):
            with self.subTest(default=default), patch.dict(os.environ, {}, clear=True):
                load_deployment_config(self.root, model_origin_default=default)
                self.assertEqual(os.environ[MODEL_ORIGIN_ENV], default)

    def test_local_launcher_without_explicit_origin_uses_public_gateway(self):
        (self.root / '.env').write_text(self.database, encoding='utf-8')
        with patch.dict(os.environ, {}, clear=True):
            load_deployment_config(self.root)
            self.assertEqual(os.environ[MODEL_ORIGIN_ENV], LOCAL_MODEL_ORIGIN)

    def test_process_configuration_overrides_local_env_and_normalizes_trailing_slash(self):
        (self.root / '.env').write_text(self.database + MODEL_ORIGIN_ENV + '=' + LOCAL_MODEL_ORIGIN + '\n', encoding='utf-8')
        with patch.dict(os.environ, {MODEL_ORIGIN_ENV: DOCKER_MODEL_ORIGIN + '/'}, clear=True):
            load_deployment_config(self.root)
            self.assertEqual(os.environ[MODEL_ORIGIN_ENV], DOCKER_MODEL_ORIGIN)

    def test_saved_gateway_switch_preserves_api_prefix_and_source_record(self):
        source = {'provider_id': 'aimarket', 'base_url': DOCKER_MODEL_ORIGIN + '/v1/', 'api_path': '/chat/completions'}
        with patch.dict(os.environ, {MODEL_ORIGIN_ENV: LOCAL_MODEL_ORIGIN}):
            resolved = with_model_gateway(source)
        self.assertEqual(resolved['base_url'] + resolved['api_path'], LOCAL_MODEL_ORIGIN + '/v1/chat/completions')
        self.assertEqual(source['base_url'], DOCKER_MODEL_ORIGIN + '/v1/')

    def test_custom_gateways_and_other_providers_keep_their_addresses(self):
        for provider, address in (('aimarket', 'https://custom.example/v1'),
                                  ('openai-compatible', DOCKER_MODEL_ORIGIN),
                                  ('volcengine-speech', 'https://openspeech.bytedance.com')):
            source = {'provider_id': provider, 'base_url': address}
            with self.subTest(provider=provider), patch.dict(os.environ, {MODEL_ORIGIN_ENV: LOCAL_MODEL_ORIGIN}):
                self.assertEqual(with_model_gateway(source), source)


if __name__ == '__main__':
    unittest.main()

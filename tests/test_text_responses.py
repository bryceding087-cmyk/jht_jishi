import io
import json
import socket
import unittest
from unittest.mock import patch
from urllib.error import URLError

import test_content_errors as fixture


class TextResponsesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture.ContentErrorTests.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        cls.directory.cleanup()

    def config(self, **extra):
        return {'api_key':'fixture-secret','base_url':'https://provider.example/compatible-mode/v1',
                'api_path':'/responses','model_id':'qwen3.8-max-0902','enable_thinking':True,**extra}

    def response(self, **extra):
        return {'status':'completed','output':[
            {'type':'reasoning','summary':[{'type':'summary_text','text':'Not the document'}]},
            {'type':'message','role':'assistant','content':[{'type':'output_text','text':'{"answer":'}, {'type':'output_text','text':'"完整剧本"}'}]}],**extra}

    def test_responses_wire_format_and_final_text(self):
        messages=[{'role':'system','content':'只返回 JSON'},{'role':'user','content':'写一个故事'}]
        with patch.object(self.host,'provider_json',return_value=self.response()) as post:
            result=self.host.llm_json(self.config(),messages)
        self.assertEqual(post.call_args.args[1],'https://provider.example/compatible-mode/v1/responses')
        self.assertEqual(post.call_args.args[3],{'model':'qwen3.8-max-0902','input':messages,'enable_thinking':True})
        self.assertEqual(self.host.parse_llm_json(result),{'answer':'完整剧本'})

    def test_chat_completions_keeps_its_existing_contract(self):
        with patch.object(self.host,'provider_json',return_value={'choices':[{'message':{'content':'answer'}}]}) as post:
            value=self.host.llm_json(self.config(api_path='/chat/completions',enable_thinking=None),[{'role':'user','content':'问题'}])
        self.assertIn('messages',post.call_args.args[3]);self.assertNotIn('input',post.call_args.args[3]);self.assertNotIn('enable_thinking',post.call_args.args[3])
        self.assertEqual(self.host.llm_text(value),'answer')

    def test_responses_image_input_is_translated_without_mutating_source(self):
        messages=[{'role':'user','content':[{'type':'text','text':'参考图'}, {'type':'image_url','image_url':{'url':'data:image/png;base64,fixture','detail':'low'}}]}]
        converted=self.host.responses_input(messages)
        self.assertEqual(converted[0]['content'][0]['type'],'input_text')
        self.assertEqual(converted[0]['content'][1],{'type':'input_image','image_url':'data:image/png;base64,fixture','detail':'low'})
        self.assertEqual(messages[0]['content'][1]['type'],'image_url')

    def test_incomplete_failed_and_refused_responses_do_not_save_partial_documents(self):
        values=[self.response(status='incomplete',incomplete_details={'reason':'max_output_tokens'}),
                self.response(status='failed',error={'message':'invalid request fixture-secret'}),
                self.response(output=[{'type':'message','content':[{'type':'refusal','refusal':'无法生成'}]}])]
        for value in values:
            with self.subTest(value=value),patch.object(self.host,'provider_json',return_value=value):
                with self.assertRaises(RuntimeError) as error:self.host.llm_json(self.config(),[])
                self.assertNotIn('fixture-secret',str(error.exception))

    def test_dns_failure_names_the_host_without_url_credentials_or_retry(self):
        with patch.object(self.host,'urlopen',side_effect=URLError(socket.gaierror(11001,'getaddrinfo failed'))) as post:
            with self.assertRaises(RuntimeError) as error:self.host.provider_json('POST','https://provider.example/api?secret=hidden','fixture-secret',{})
        message=str(error.exception)
        self.assertIn('provider.example',message);self.assertIn('Base URL',message)
        self.assertNotIn('fixture-secret',message);self.assertNotIn('secret=hidden',message)
        post.assert_called_once()

    def test_thinking_setting_can_be_saved_disabled_and_reset(self):
        config=self.host.create_registry_model(self.host.RegistryModelCreate(kind='text',name='千问',provider_id='openai-compatible',**self.config()))
        self.assertTrue(config['enable_thinking'])
        for value in (False,None):
            config=self.host.update_registry_model('text',config['id'],self.host.RegistryModelUpdate(enable_thinking=value))
            self.assertIs(config['enable_thinking'],value)
        self.assertEqual(self.host.internal_registry_model('text',config['id'])['api_key'],'fixture-secret')

    def test_saved_text_timeout_reaches_transport_and_preserves_model_credentials(self):
        created = self.host.create_registry_model(self.host.RegistryModelCreate(
            kind='text', name='长剧本千问', provider_id='openai-compatible', **self.config()))
        public = self.host.update_registry_model('text', created['id'], self.host.RegistryModelUpdate(timeout_seconds=900))
        self.assertEqual(public['timeout_seconds'], 900)
        config = self.host.internal_registry_model('text', created['id'])
        self.assertTrue(config['enable_thinking'])
        self.assertEqual(config['api_key'], 'fixture-secret')
        self.assertEqual(config['api_path'], '/responses')
        with patch.object(self.host, 'urlopen', return_value=io.BytesIO(json.dumps(self.response()).encode())) as post:
            result = self.host.llm_json(config, [{'role': 'user', 'content': '继续写第 2 集'}])
        self.assertEqual(self.host.parse_llm_json(result), {'answer': '完整剧本'})
        post.assert_called_once()
        self.assertEqual(post.call_args.kwargs['timeout'], 900)

    def test_long_text_timeout_never_reposts_and_reports_configured_limit(self):
        for failure in (TimeoutError('read timed out'), URLError(socket.timeout('timed out'))):
            with self.subTest(failure=failure), patch.object(self.host, 'urlopen', side_effect=failure) as post:
                with self.assertRaisesRegex(RuntimeError, '读取超时（900秒）'):
                    self.host.llm_json(self.config(timeout_seconds=900), [])
                post.assert_called_once()


if __name__ == '__main__': unittest.main()

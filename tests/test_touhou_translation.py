"""Regression checks without loading AstrBot or contacting a paid model.

Run: python -m unittest discover -s tests -v
"""
import ast
import asyncio
import datetime
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import time
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("touhou_under_test", ROOT / "touhou.py")
feed = importlib.util.module_from_spec(spec)
spec.loader.exec_module(feed)
namespace = dict(asyncio=asyncio, datetime=datetime, hashlib=hashlib, json=json,
                 time=time, logger=logging.getLogger("translation-test"),
                 parse_translations=feed.parse_translations)
tree = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
plugin = next(node for node in tree.body if isinstance(node, ast.ClassDef))
methods = {"_translate_touhou_data", "_touhou_text", "_to_int", "_cache_get", "_cache_set"}
body = [node for node in plugin.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in methods]
isolated = ast.ClassDef(name="Harness", bases=[], keywords=[], body=body, decorator_list=[])
exec(compile(ast.fix_missing_locations(ast.Module(body=[isolated], type_ignores=[])), "actual_translation_methods", "exec"), namespace)

class Provider:
    def __init__(self, provider_id="chat"):
        self.provider_id = provider_id
        self.model = "model-one"
        self.calls = 0
    def meta(self):
        return SimpleNamespace(id=self.provider_id)
    def get_model(self):
        return self.model
    async def text_chat(self, **kwargs):
        self.calls += 1
        return SimpleNamespace(completion_text='[{"id":0,"title":"旧接口译文"},{"id":1,"title":"第二条"}]')

class Context:
    def __init__(self):
        self.provider = Provider()
        self.calls = []
        self.reply = '[{"id":1,"title":"东方新周边"},{"id":0,"title":"博丽神社活动"}]'
    async def get_using_provider_async(self):
        return self.provider
    def get_provider_by_id(self, provider_id):
        return self.provider if provider_id == self.provider.provider_id else None
    async def llm_generate(self, **kwargs):
        self.calls.append(kwargs)
        await asyncio.sleep(0)
        return SimpleNamespace(completion_text=self.reply)

def setup():
    harness = namespace["Harness"]()
    harness.config = {}
    harness._cache = {}
    harness.context = Context()
    data = {"items": [
        {"title":"博麗神社イベント", "date":"2026-11-05", "source":"RSS", "url":"https://example.com/1"},
        {"title":"東方新グッズ", "date":"2026-11-04", "source":"RSS", "url":"https://example.com/2"},
    ], "error":None, "warnings":[]}
    return harness, data

class TranslationTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_mapping_concurrent_cache_and_original(self):
        h, raw = setup()
        first, second = await asyncio.gather(h._translate_touhou_data(raw), h._translate_touhou_data(raw))
        self.assertEqual(first["items"][0]["translated_title"], "博丽神社活动")
        self.assertEqual(second["items"][1]["translated_title"], "东方新周边")
        self.assertEqual(len(h.context.calls), 1)
        self.assertEqual(h.context.calls[0]["contexts"], [])
        self.assertEqual(len(json.loads(h.context.calls[0]["prompt"])), 2)
        self.assertNotIn("example.com", h.context.calls[0]["prompt"])
        self.assertNotIn("translated_title", raw["items"][0])
        self.assertEqual(first["items"][0]["date"], raw["items"][0]["date"])
        self.assertIn("原文：博麗神社イベント", h._touhou_text(first))
        h.config["touhou_translation_show_original"] = False
        self.assertNotIn("原文：", h._touhou_text(await h._translate_touhou_data(raw)))

    async def test_toggle_provider_and_model_change(self):
        h, raw = setup()
        await h._translate_touhou_data(raw)
        h.config["touhou_translate"] = False
        self.assertNotIn("translated_title", (await h._translate_touhou_data(raw))["items"][0])
        self.assertEqual(len(h.context.calls), 1)
        h.config["touhou_translate"] = True
        h.context.provider.model = "model-two"
        await h._translate_touhou_data(raw)
        self.assertEqual(len(h.context.calls), 2)
        h.config["touhou_translation_provider_id"] = "missing"
        missing = await h._translate_touhou_data(raw)
        self.assertIn("未配置", missing["translation_note"])
        self.assertEqual(missing["items"], raw["items"])

    async def test_invalid_response_cooldown_and_retry(self):
        h, raw = setup()
        h.context.reply = '[{"id":0,"title":"一"},{"id":0,"title":"二"}]'
        failed = await h._translate_touhou_data(raw)
        self.assertIn("translation_note", failed)
        self.assertEqual(failed["items"], raw["items"])
        await h._translate_touhou_data(raw)
        self.assertEqual(len(h.context.calls), 1)
        for key, (timestamp, value) in list(h._cache.items()):
            h._cache[key] = (timestamp - 61, value)
        h.context.reply = '[{"id":0,"title":"博丽神社活动"},{"id":1,"title":"东方新周边"}]'
        retried = await h._translate_touhou_data(raw)
        self.assertIn("translated_title", retried["items"][0])
        self.assertEqual(len(h.context.calls), 2)

    async def test_legacy_provider_and_disabled_model(self):
        h, raw = setup()
        provider = Provider()
        h.context = SimpleNamespace(get_using_provider=lambda: provider)
        result = await h._translate_touhou_data(raw)
        self.assertEqual(result["items"][0]["translated_title"], "旧接口译文")
        h.context = SimpleNamespace(get_using_provider=lambda: None)
        self.assertIn("translation_note", await h._translate_touhou_data(raw))

    def test_parser_accepts_fences_and_rejects_bad_batch(self):
        self.assertEqual(feed.parse_translations('```json\n[{"id":0,"title":"中文"}]\n```', 1), ["中文"])
        for value in ['[]', '[{"id":true,"title":"中文"}]', '[{"id":0,"title":""}]', 'not json']:
            with self.assertRaises(ValueError):
                feed.parse_translations(value, 1)

if __name__ == "__main__":
    unittest.main()

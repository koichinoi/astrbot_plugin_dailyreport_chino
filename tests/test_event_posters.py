"""Poster edition matching and offline embedding; no external requests."""
import ast
import asyncio
import importlib.util
import logging
from pathlib import Path
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('poster_events',ROOT/'touhou_events.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
ns=dict(module.__dict__,asyncio=asyncio,logger=logging.getLogger('poster-test'))
tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
plugin=next(node for node in tree.body if isinstance(node,ast.ClassDef))
methods=[node for node in plugin.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in {'_embed_touhou_event_covers','_to_int'}]
cls=ast.ClassDef(name='Harness',bases=[],keywords=[],body=methods,decorator_list=[])
exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),'actual_cover_methods','exec'),ns)

def poster(alt,url,extra=''):
    return f'<li class="gallerybox"><a><img alt="{alt}" src="https://upload.thbwiki.cc/{url}" {extra}/></a></li>'

HTML='<img alt="LOGO" src="https://upload.thbwiki.cc/logo.png"/>'+poster('第1届 宣传图1','old.webp')+poster('第二届 宣传图1','new.webp','srcset="https://upload.thbwiki.cc/new-2x.webp 2x"')
URL='https://thwiki.cc/Example#ev-2'

def setup(**config):
    h=ns['Harness']();h.config=config;h._cache={};h.calls=[];h._cache_ttl=lambda:900
    h._cache_get=lambda key,ttl:h._cache.get(key)
    h._cache_set=lambda key,value:h._cache.__setitem__(key,value)
    async def get_json(url,retries=1):h.calls.append(('page',url));return {'parse':{'text':HTML}}
    async def image(url):h.calls.append(('image',url));return 'data:image/webp;base64,ZmFrZQ=='
    h._get_json=get_json;h._fetch_image_b64=image
    return h

class PosterTests(unittest.IsolatedAsyncioTestCase):
    def test_edition_match_never_selects_logo_or_previous_edition(self):
        self.assertEqual(module.event_poster_urls(HTML,URL),['https://upload.thbwiki.cc/new-2x.webp'])
        self.assertEqual(module.event_poster_urls(HTML,'https://thwiki.cc/Example#ev-3'),[])
        self.assertEqual(module.event_poster_urls(HTML,'https://thwiki.cc/Example'),[])
        self.assertEqual(module._edition('二十一'),21)
        self.assertTrue(module.event_page_api(URL).startswith('https://thwiki.cc/api.php?'))
        self.assertEqual(module.event_page_api('https://other.example/Example#ev-2'),'')
        self.assertEqual(module.event_poster_urls(poster('第2届','x','srcset="https://upload.thbwiki.cc/x 1.2.3x"'),URL),['https://upload.thbwiki.cc/x'])

    async def test_embedding_copies_source_and_caches_page(self):
        h=setup();data={'touhou_events':{'items':[{'url':URL,'title':'展会'}]}}
        result=await h._embed_touhou_event_covers(data)
        self.assertTrue(result['touhou_events']['items'][0]['cover_img'].startswith('data:image/'))
        self.assertNotIn('cover_img',data['touhou_events']['items'][0])
        await h._embed_touhou_event_covers(data)
        self.assertEqual(sum(kind=='page' for kind,_ in h.calls),1)

    async def test_disabled_limit_missing_and_failures_keep_events(self):
        data={'touhou_events':{'items':[{'url':URL,'title':'展会'},{'url':URL,'title':'第二场'}]}}
        for config in [{'touhou_event_covers':False},{'touhou_event_cover_max':0}]:
            h=setup(**config);result=await h._embed_touhou_event_covers(data)
            self.assertEqual(h.calls,[]);self.assertEqual(len(result['touhou_events']['items']),2)
        h=setup(touhou_event_cover_max=1)
        result=await h._embed_touhou_event_covers(data)
        self.assertIn('cover_img',result['touhou_events']['items'][0]);self.assertNotIn('cover_img',result['touhou_events']['items'][1])
        async def broken(url,retries=1):raise TimeoutError()
        h._get_json=broken;h._cache={}
        result=await h._embed_touhou_event_covers(data)
        self.assertEqual(result['touhou_events']['items'],data['touhou_events']['items'])
        h=setup()
        result=await h._embed_touhou_event_covers({'touhou_events':{'items':[{'url':URL.replace('ev-2','ev-3'),'title':'无海报'}]}})
        self.assertNotIn('cover_img',result['touhou_events']['items'][0])

if __name__=='__main__':unittest.main()

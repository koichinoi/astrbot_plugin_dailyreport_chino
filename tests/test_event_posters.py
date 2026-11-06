"""Poster edition matching and offline embedding; no external requests."""
import ast
import asyncio
import base64
import importlib.util
import logging
from pathlib import Path
import unittest
from types import SimpleNamespace

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('poster_events',ROOT/'touhou_events.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
ns=dict(module.__dict__,asyncio=asyncio,base64=base64,logger=logging.getLogger('poster-test'),VERSION='v2.7.1',BROWSER_UA='test-browser',AstrMessageEvent=object)
tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
plugin=next(node for node in tree.body if isinstance(node,ast.ClassDef))
methods=[node for node in plugin.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in {'_embed_touhou_event_covers','_fetch_event_cover','_render_touhou_events_card','cmd_touhou_events','_touhou_events_text','_to_int'}]
for method in methods:
    if method.name == 'cmd_touhou_events':method.decorator_list=[]
cls=ast.ClassDef(name='Harness',bases=[],keywords=[],body=methods,decorator_list=[])
exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),'actual_cover_methods','exec'),ns)
ns['TOUHOU_EVENTS_SECTION_TMPL']=(ROOT/'templates/touhou_events.html').read_text(encoding='utf-8')
assignment=next(n for n in tree.body if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='TOUHOU_EVENTS_CARD_TMPL' for t in n.targets))
exec(compile(ast.fix_missing_locations(ast.Module(body=[assignment],type_ignores=[])),'actual_card_template','exec'),ns)

def poster(alt,url,extra=''):
    return f'<li class="gallerybox"><a><img alt="{alt}" src="https://upload.thbwiki.cc/{url}" {extra}/></a></li>'

HTML='<img alt="LOGO" src="https://upload.thbwiki.cc/logo.png"/>'+poster('第1届 宣传图1','old.webp')+poster('第二届 宣传图1','new.webp','srcset="https://upload.thbwiki.cc/new-2x.webp 2x"')
URL='https://thwiki.cc/Example#ev-2'

def setup(**config):
    h=ns['Harness']();h.config=config;h._cache={};h.calls=[];h._cache_ttl=lambda:900
    h._cache_get=lambda key,ttl:h._cache.get(key)
    h._cache_set=lambda key,value:h._cache.__setitem__(key,value)
    async def get_json(url,retries=1):h.calls.append(('page',url));return {'parse':{'text':HTML}}
    async def image(url,referer):h.calls.append(('image',url));return 'data:image/webp;base64,ZmFrZQ==','已加载'
    h._get_json=get_json;h._fetch_event_cover=image
    return h

class PosterTests(unittest.IsolatedAsyncioTestCase):
    async def test_command_image_card_links_and_text_fallback(self):
        h=setup();data={'days':60,'region_label':'浙江','type_label':'展会','checked_date':'2026-11-06','error':None,'items':[{'url':URL,'title':'活动','date':'2026-11-07','days_left':1,'kind':'展会','location':'台州'}]}
        requests=[]
        async def gather(region):requests.append(region);return data
        async def render(template,values):
            self.assertIn('events-wrap',template)
            self.assertTrue(values['touhou_events']['items'][0]['cover_img'].startswith('data:'))
            return 'rendered-image'
        h._touhou_events_data=gather;h.html_render=render;h._render_scale=lambda:2
        h._strip=lambda message,names:message.split(' ',1)[1]
        event=SimpleNamespace(message_str='/东方漫展 浙江',image_result=lambda value:('image',value),plain_result=lambda value:('text',value))
        responses=[r async for r in h.cmd_touhou_events(event)]
        self.assertEqual(requests,['浙江']);self.assertEqual(responses[0],('image','rendered-image'));self.assertIn(URL,responses[1][1])
        h.config['touhou_events_image']=False
        responses=[r async for r in h.cmd_touhou_events(event)]
        self.assertEqual(len(responses),1);self.assertEqual(responses[0][0],'text')

    async def test_downloader_headers_image_validation_and_http_diagnostic(self):
        captured=[]
        state={'status':200,'raw':b'RIFF1234WEBPfake'}
        class Response:
            @property
            def status(self):return state['status']
            @property
            def content(self):return self
            async def iter_chunked(self,size):yield state['raw']
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
        class Session:
            async def __aenter__(self):return self
            async def __aexit__(self,*args):pass
            def get(self,url,headers,proxy):captured.append(headers);return Response()
        old=ns.get('aiohttp');ns['aiohttp']=SimpleNamespace(ClientTimeout=lambda **kwargs:None,ClientSession=lambda **kwargs:Session())
        try:
            h=setup();get=ns['Harness']._fetch_event_cover
            image,status=await get(h,'https://upload.thbwiki.cc/poster.webp',URL)
            self.assertTrue(image.startswith('data:image/webp;base64,'));self.assertEqual(captured[0]['Referer'],'https://thwiki.cc/Example')
            state['status']=403
            image,status=await get(h,'https://upload.thbwiki.cc/denied.webp',URL)
            self.assertEqual(image,'');self.assertIn('HTTP 403',status)
            state.update(status=200,raw=b'<html>not an image</html>')
            image,status=await get(h,'https://upload.thbwiki.cc/invalid.webp',URL)
            self.assertEqual(image,'');self.assertIn('有效海报',status)
        finally:
            if old is None:ns.pop('aiohttp',None)
            else:ns['aiohttp']=old

    def test_edition_match_never_selects_logo_or_previous_edition(self):
        self.assertEqual(module.event_poster_urls(HTML,URL),['https://upload.thbwiki.cc/new-2x.webp','https://upload.thbwiki.cc/new.webp'])
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
        self.assertEqual([i['title'] for i in result['touhou_events']['items']],['展会','第二场'])
        self.assertNotIn('cover_img',result['touhou_events']['items'][0])
        self.assertIn('TimeoutError',result['touhou_events']['items'][0]['cover_status'])
        h=setup()
        result=await h._embed_touhou_event_covers({'touhou_events':{'items':[{'url':URL.replace('ev-2','ev-3'),'title':'无海报'}]}})
        self.assertNotIn('cover_img',result['touhou_events']['items'][0])

if __name__=='__main__':unittest.main()

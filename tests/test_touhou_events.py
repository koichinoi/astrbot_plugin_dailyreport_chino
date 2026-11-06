"""Public schedule parsing, date filtering and plugin integration checks."""
import ast
import asyncio
import datetime
import importlib.util
import logging
from pathlib import Path
import re
import time
import unittest
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location("events_under_test",ROOT/"touhou_events.py")
events=importlib.util.module_from_spec(spec);spec.loader.exec_module(events)
namespace=dict(events.__dict__,asyncio=asyncio,datetime=datetime,re=re,time=time,urlsplit=urlsplit,logger=logging.getLogger("event-test"))
tree=ast.parse((ROOT/"main.py").read_text(encoding="utf-8"))
plugin=next(node for node in tree.body if isinstance(node,ast.ClassDef))
names={"_touhou_events_data","_touhou_events_text","_cache_get","_cache_set","_cache_ttl","_to_int"}
methods=[node for node in plugin.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in names]
cls=ast.ClassDef(name="Harness",bases=[],keywords=[],body=methods,decorator_list=[])
exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),"actual_event_methods","exec"),namespace)

def item(date,kind="展会",title="东方Only",location="浙江省台州市",url="/event#1"):
    return f'<li><b><a href="{url}">{date}</a></b> <b>{kind}</b> {title} 于 {location} 举办</li>'

FIXTURE='<h2><span class="mw-headline">中国</span></h2><ul>'+item("2026年11月05日",url="/past")+item("2026年11月06日",url="/today")+item("2026年11月07日","聚会/展会",url="/tomorrow")+item("2026年11月07日","聚会",url="/party")+item("2026年11月08日",location="上海市",url="/shanghai")+item("2026年11月08日",location="上海市",url="/shanghai")+'</ul><h2><span class="mw-headline">日本</span></h2><ul>'+item("2026年11月07日",location="东京",url="/japan")+'</ul>'

class EventTests(unittest.IsolatedAsyncioTestCase):
    def test_real_structure_country_type_range_and_duplicates(self):
        parsed=events.parse_schedule(FIXTURE)
        selected=events.filter_events(parsed,today=datetime.date(2026,11,6),days=2)
        self.assertEqual([row['date'] for row in selected],['2026-11-06','2026-11-07','2026-11-08'])
        self.assertEqual([row['days_left'] for row in selected],[0,1,2])
        self.assertNotIn('东京',[row['location'] for row in selected])
        nearby=events.filter_events(parsed,today=datetime.date(2026,11,6),days=2,regions=['台州'])
        self.assertEqual(len(nearby),2)
        self.assertEqual(len(events.filter_events(parsed,today=datetime.date(2026,11,6),days=2,types=[])),4)
        self.assertEqual(len(events.filter_events(parsed,today=datetime.date(2026,11,6),days=2,limit=1)),1)

    def test_invalid_source_and_unsafe_link(self):
        with self.assertRaises(ValueError):events.parse_schedule('<html>Access denied</html>')
        bad='<h2><span class="mw-headline">中国</span></h2>'+item('2026年11月07日',url='javascript:bad()')
        with self.assertRaises(ValueError):events.parse_schedule(bad)

    async def test_cache_region_override_and_cross_day(self):
        h=namespace['Harness']();h.config={'touhou_event_regions':['台州']};h._cache={};calls=[]
        clock={'today':datetime.date(2026,11,6)};old=namespace['china_today'];namespace['china_today']=lambda:clock['today']
        async def get_json(url):calls.append(url);return {'parse':{'text':FIXTURE}}
        h._get_json=get_json
        try:
            local=await h._touhou_events_data();self.assertEqual(len(local['items']),2)
            all_regions=await h._touhou_events_data('全国');self.assertEqual(len(all_regions['items']),3)
            empty=await h._touhou_events_data('宁波');self.assertEqual(empty['items'],[]);self.assertIsNone(empty['error'])
            self.assertEqual(h.config['touhou_event_regions'],['台州']);self.assertEqual(len(calls),1)
            clock['today']=datetime.date(2026,11,9)
            self.assertEqual((await h._touhou_events_data('全国'))['items'],[]);self.assertEqual(len(calls),1)
            self.assertIn('详情：https://thwiki.cc/today',h._touhou_events_text(local))
        finally:namespace['china_today']=old

    async def test_failure_is_distinct_from_empty_results(self):
        h=namespace['Harness']();h.config={};h._cache={}
        async def bad(url):raise TimeoutError()
        h._get_json=bad
        result=await h._touhou_events_data();self.assertTrue(result['error']);self.assertEqual(result['items'],[])
        self.assertIn('读取失败',h._touhou_events_text(result))

if __name__=='__main__':unittest.main()

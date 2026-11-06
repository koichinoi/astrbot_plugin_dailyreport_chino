"""Subscription routing tests. All platform IDs, group IDs and sends are fake."""
import ast
import asyncio
import importlib.util
import logging
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('subscriptions_under_test',ROOT/'subscriptions.py')
subscriptions=importlib.util.module_from_spec(spec);spec.loader.exec_module(subscriptions)
ns=dict(subscriptions.__dict__,AstrMessageEvent=object,logger=logging.getLogger('subscription-test'))
tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
plugin=next(node for node in tree.body if isinstance(node,ast.ClassDef))
names={'_target_list','_qq_platform_ids','_group_bindings','cmd_sub','cmd_unsub','_push_job'}
methods=[node for node in plugin.body if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef)) and node.name in names]
for node in methods:node.decorator_list=[]
cls=ast.ClassDef(name='Harness',bases=[],keywords=[],body=methods,decorator_list=[])
exec(compile(ast.fix_missing_locations(ast.Module(body=[cls],type_ignores=[])),'actual_subscription_methods','exec'),ns)

def setup(config=None,ids=('qq-main',)):
    h=ns['Harness']();h.config=dict(config or {});h.saved=0
    h.context=SimpleNamespace(platform_manager=SimpleNamespace(platform_insts=[SimpleNamespace(meta=lambda key=key:SimpleNamespace(id=key,name='aiocqhttp')) for key in ids]))
    h._is_admin=lambda event:True
    def save():h.saved+=1
    h._save=save
    return h

def event(umo):return SimpleNamespace(unified_msg_origin=umo,plain_result=lambda text:text)

class SubscriptionTests(unittest.IsolatedAsyncioTestCase):
    def test_single_platform_and_deduplication(self):
        h=setup({'push_group_ids':['123456','0123456','123456'],'push_targets':['qq-main:GroupMessage:123456','other:FriendMessage:user']})
        self.assertEqual(h._target_list(),['qq-main:GroupMessage:123456','other:FriendMessage:user'])
        h.config['push_group_ids']=[]
        self.assertEqual(len(h._target_list()),2)

    def test_multiple_accounts_require_selection(self):
        h=setup({'push_group_ids':['123456']},ids=('qq-main','qq-second'))
        self.assertEqual(h._target_list(),[]);self.assertTrue(h._subscription_warnings)
        h.config['push_group_platform_id']='qq-second'
        self.assertEqual(h._target_list(),['qq-second:GroupMessage:123456'])
        h.config['push_group_ids']=['qq-main|123456','123456']
        self.assertEqual(h._target_list(),['qq-main:GroupMessage:123456','qq-second:GroupMessage:123456'])

    def test_invalid_groups_and_unavailable_platforms(self):
        h=setup({'push_group_ids':['not-a-group','0','telegram|123456','123456']})
        self.assertEqual(h._target_list(),['qq-main:GroupMessage:123456']);self.assertTrue(h._subscription_warnings)
        h=setup({'push_group_ids':['123456']},ids=())
        self.assertEqual(h._target_list(),[])

    async def test_subscribe_does_not_materialize_ui_groups(self):
        h=setup({'push_group_ids':['123456']})
        replies=[message async for message in h.cmd_sub(event('qq-main:GroupMessage:123456'))]
        self.assertIn('已经',replies[0]);self.assertNotIn('push_targets',h.config)
        h.config['push_group_ids']=[];self.assertEqual(h._target_list(),[])
        h.config['push_group_ids']=['123456']
        _=[message async for message in h.cmd_sub(event('qq-main:GroupMessage:654321'))]
        self.assertNotIn('push_targets',h.config)
        self.assertEqual(h.config['push_group_ids'],['123456','qq-main|654321'])
        h.config['push_group_ids']=[];self.assertEqual(h._target_list(),[])
        _=[message async for message in h.cmd_sub(event('qq-main:FriendMessage:user'))]
        self.assertEqual(h.config['push_targets'],['qq-main:FriendMessage:user'])

    async def test_unsubscribe_removes_both_lists_only_for_same_account(self):
        h=setup({'push_group_ids':['qq-main|123456','qq-second|123456'],'push_targets':['qq-main:GroupMessage:123456','qq-main:FriendMessage:123456']},ids=('qq-main','qq-second'))
        _=[message async for message in h.cmd_unsub(event('qq-main:GroupMessage:123456'))]
        self.assertEqual(h.config['push_group_ids'],['qq-second|123456'])
        self.assertEqual(h.config['push_targets'],['qq-main:FriendMessage:123456'])
        self.assertEqual(h.saved,1)

    async def test_push_sends_once_per_effective_target_with_fake_transport(self):
        h=setup({'push_group_ids':['123456','123456'],'push_targets':['qq-main:GroupMessage:123456','other:FriendMessage:user']})
        sent=[]
        async def send(umo,chain):sent.append(umo)
        async def gather():return {}
        async def render(data):return 'fake-image'
        h.context.send_message=send;h._gather=gather;h._render_themed=render
        h._report_text=lambda data:'fake report';h._news_images=lambda data:[];h._img_chain=lambda src:src
        await h._push_job()
        self.assertEqual(sent,['qq-main:GroupMessage:123456','other:FriendMessage:user'])

if __name__=='__main__':unittest.main()

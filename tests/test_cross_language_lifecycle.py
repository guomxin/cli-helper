"""Real Node proxy/coordinator -> HTTP MCP -> central service -> SQLite."""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import unittest

ROOT=Path(__file__).resolve().parents[1]


class Actor:
    def __init__(self, args, env=None):
        self.process=subprocess.Popen(args,cwd=ROOT,env=env,stdin=subprocess.PIPE,stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE,text=True,encoding='utf-8')
        self.output=queue.Queue(); self.errors=[]
        def collect():
            for line in self.process.stdout: self.output.put(line)
            self.output.put(None)
        def errors():
            for line in self.process.stderr: self.errors.append(line)
        threading.Thread(target=collect,daemon=True).start()
        threading.Thread(target=errors,daemon=True).start()
    def receive(self):
        try:
            line=self.output.get(timeout=30)
            if line is None: raise AssertionError('Fixture exited: '+''.join(self.errors)[-3000:])
            return json.loads(line)
        except queue.Empty: raise AssertionError('Fixture timed out: '+''.join(self.errors)[-3000:])
    def send(self, message):
        self.process.stdin.write(json.dumps(message)+'\n'); self.process.stdin.flush()
        result=self.receive()
        if not result.get('ok'): raise AssertionError(result)
        return result
    def close(self):
        if self.process.stdin.closed: return
        self.process.terminate()
        try: self.process.wait(timeout=10)
        except subprocess.TimeoutExpired: self.process.kill(); self.process.wait(timeout=5)
        for stream in (self.process.stdin,self.process.stdout,self.process.stderr): stream.close()


class CrossLanguageLifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name)
        (self.home/'session.key').write_bytes(os.urandom(32))
        (self.home/'session.key').chmod(0o600)
        # Isolate the encryption key as well as databases and browser profiles.
        self.env=dict(os.environ,PYTHONPATH=str(ROOT),AGENTBRIDGE_SESSION_KEY_FILE=str(self.home/'session.key'))
        self.start_server()
        self.start_host()
    def start_host(self):
        self.host=Actor(['node',str(ROOT/'tests/fixtures/lifecycle_host.mjs')])
        self.addCleanup(self.host.close)
    def start_server(self, port=0):
        self.server=Actor([sys.executable,'-u',str(ROOT/'tests/fixtures/lifecycle_server.py'),'--home',str(self.home),'--port',str(port)],self.env)
        self.addCleanup(self.server.close)
        self.connection=self.server.receive()
    def call(self, action, user='alice', **kwargs):
        return self.host.send({'action':action,'user':user,'token':self.connection['tokens'][user],
                              'endpoint':f"http://127.0.0.1:{self.connection['port']}/mcp",**kwargs})
    def query(self, sql, *parameters):
        return self.server.send({'action':'query','sql':sql,'parameters':parameters})['result']
    def activate(self,user='alice',system='oa'):
        return self.server.send({'action':'activate','user':user,'system':system})

    def test_two_reads_have_distinct_terminal_tasks_and_operations(self):
        self.activate()
        for index in (1,2):
            result=self.call('read',tool='oa_workflow_pending_list',callId=f'read-{index}',arguments={'limit':index})
            self.assertIn('succeeded',json.dumps(result))
        tasks=self.query('SELECT task_id,status FROM agent_tasks')
        self.assertEqual(len(tasks),2)
        self.assertEqual({t['status'] for t in tasks},{'succeeded'})
        self.assertEqual(len(self.query('SELECT * FROM task_operations')),2)
        self.assertEqual([json.loads(r['arguments']) for r in self.query('SELECT * FROM fixture_calls')],[{'limit':1},{'limit':2}])

    def test_login_resume_uses_original_parameters_and_deduplicates(self):
        endpoint=self.server.send({'action':'notification_endpoint','user':'alice'})['result']['endpointId']
        first=self.call('read',tool='oa_workflow_pending_list',callId='needs-login',arguments={'limit':7,'keyword':'fixture'})
        self.assertEqual(len(first['records']),1,first)
        record=first['records'][0]
        self.activate()
        self.call('resume',**record)
        self.call('resume',**record)
        self.assertEqual(len(self.query('SELECT * FROM fixture_calls')),1)
        self.assertEqual(json.loads(self.query('SELECT arguments FROM fixture_calls')[0]['arguments']),{'limit':7,'keyword':'fixture'})
        self.assertEqual(self.query('SELECT status FROM agent_tasks')[0]['status'],'succeeded')
        self.assertEqual(len(self.query("SELECT * FROM user_timeline WHERE dedupe_key LIKE 'message:login-read:%'")),1)
        outbox=self.query("SELECT * FROM notification_outbox WHERE event_id IN (SELECT entry_id FROM user_timeline WHERE dedupe_key LIKE 'message:login-read:%') AND endpoint_id=?",endpoint)
        self.assertEqual(len(outbox),1)
        arguments={'agent_host':'openclaw','endpoint_key':'fixture-notify:alice','limit':100}
        claimed=self.call('call',tool='agentbridge_host_notification_claim',arguments=arguments)['result']
        self.assertFalse(claimed.get('isError'),claimed)
        ack={'agent_host':'openclaw','endpoint_key':'fixture-notify:alice','delivery_id':outbox[0]['delivery_id'],'succeeded':True}
        for _ in range(2):
            result=self.call('call',tool='agentbridge_host_notification_ack',arguments=ack)['result']
            self.assertFalse(result.get('isError'),result)
        self.assertEqual(self.query('SELECT state FROM notification_outbox WHERE delivery_id=?',outbox[0]['delivery_id'])[0]['state'],'acknowledged')

    def test_login_recovery_isolated_by_user_and_system(self):
        oa=self.call('read',tool='oa_workflow_pending_list',callId='oa-login',arguments={'limit':3})['records'][0]
        other=self.call('call',user='bob',tool='agentbridge_interaction_get',arguments={'interaction_id':oa['interactionId']})['result']
        self.assertTrue(other.get('isError'),other)
        self.call('read',tool='smartlight_system_overview',callId='light-login')
        bindings=self.query('SELECT t.task_id,i.interaction_id,i.system_id FROM task_interactions t JOIN interactions i USING(interaction_id)')
        self.assertEqual({b['system_id'] for b in bindings},{'oa','smartlight'})
        self.activate()
        self.call('resume',**oa)
        self.assertEqual(sorted(t['status'] for t in self.query('SELECT status FROM agent_tasks')),['succeeded','waiting_user'])
        self.activate(system='smartlight')
        light=next(b for b in bindings if b['system_id']=='smartlight')
        self.call('resume',interactionId=light['interaction_id'],taskId=light['task_id'])
        self.assertEqual({t['status'] for t in self.query('SELECT status FROM agent_tasks')},{'succeeded'})
        self.assertEqual({r['system'] for r in self.query('SELECT * FROM fixture_calls')},{'oa','smartlight'})

    def test_two_pending_reads_keep_distinct_recovery_ownership(self):
        first=self.call('reads',reads=[{'tool':'oa_workflow_pending_list','callId':f'parallel-{i}','arguments':{'limit':i}} for i in (2,3)])
        bindings=self.query('SELECT task_id,interaction_id FROM task_interactions')
        self.assertEqual(len(bindings),2,first)
        self.assertEqual(len({b['interaction_id'] for b in bindings}),2)
        self.assertEqual(len(self.query('SELECT * FROM auth_challenges')),1)
        self.activate()
        for b in bindings: self.call('resume',interactionId=b['interaction_id'],taskId=b['task_id'])
        self.assertEqual(len(self.query('SELECT * FROM fixture_calls')),2)
        self.assertEqual({t['status'] for t in self.query('SELECT status FROM agent_tasks')},{'succeeded'})

    def test_cancel_during_login_does_not_reactivate_query(self):
        first=self.call('read',tool='oa_workflow_pending_list',callId='cancel-me',arguments={'limit':2})
        record=first['records'][0]
        canceled=self.call('call',tool='agentbridge_task_cancel',arguments={'task_id':record['taskId']})
        self.assertEqual(self.query('SELECT status FROM agent_tasks')[0]['status'],'canceled',canceled)
        self.activate()
        self.call('resume',**record)
        self.assertEqual(self.query('SELECT status FROM agent_tasks')[0]['status'],'canceled')
        self.assertEqual(self.query('SELECT * FROM fixture_calls'),[])

    def test_central_and_host_restart_recover_from_persisted_facts(self):
        first=self.call('read',tool='oa_workflow_pending_list',callId='restart',arguments={'limit':9})
        record=first['records'][0]
        self.activate()
        port=self.connection['port']
        self.server.close()
        self.start_server(port)
        self.host.close()
        self.start_host()
        self.call('resume',**record)
        self.assertEqual(self.query('SELECT status FROM agent_tasks')[0]['status'],'succeeded')
        self.assertEqual(len(self.query('SELECT * FROM fixture_calls')),1)

    def test_two_users_two_systems_do_not_share_tasks_or_arguments(self):
        for user,system,tool,arguments in [('alice','oa','oa_workflow_pending_list',{'limit':2}),
                ('bob','smartlight','smartlight_system_overview',{}),('bob','oa','oa_workflow_pending_list',{'limit':8})]:
            self.activate(user,system)
            self.call('read',user=user,tool=tool,callId=f'{user}-{system}',arguments=arguments)
        calls=self.query('SELECT * FROM fixture_calls ORDER BY rowid')
        self.assertEqual([(r['owner'],r['system']) for r in calls],[('alice','oa'),('bob','smartlight'),('bob','oa')])
        self.assertEqual(len(self.query('SELECT * FROM agent_tasks')),3)

    def test_restart_before_login_and_cancel_one_of_shared_waiters(self):
        self.call('reads',reads=[{'tool':'oa_workflow_pending_list','callId':f'share-{i}','arguments':{'limit':i}} for i in (4,5)])
        bindings=self.query('SELECT task_id,interaction_id FROM task_interactions ORDER BY task_id')
        self.assertEqual(len(bindings),2)
        self.call('call',tool='agentbridge_task_cancel',arguments={'task_id':bindings[0]['task_id']})
        port=self.connection['port']; self.server.close(); self.start_server(port)
        self.host.close(); self.start_host()
        self.activate()
        for b in bindings:
            self.call('resume',interactionId=b['interaction_id'],taskId=b['task_id'])
        self.assertEqual(sorted(t['status'] for t in self.query('SELECT status FROM agent_tasks')),['canceled','succeeded'])
        self.assertEqual(len(self.query('SELECT * FROM fixture_calls')),1)

    def test_lease_renewal_expiry_takeover_rejects_stale_holder(self):
        first=self.call('read',tool='oa_workflow_pending_list',callId='lease',arguments={'limit':2})
        record=first['records'][0]; task=record['taskId']
        def lease(**kwargs):
            return self.call('call',tool='agentbridge_host_coordinator_lease_acquire',arguments={'task_id':task,**kwargs})['result']
        current=lease()['coordinatorLease']
        self.assertEqual(lease()['coordinatorLease']['version'],current['version'])
        registered=self.call('call',tool='agentbridge_host_register',registration=True,hostInstanceId='fixture-takeover')['result']
        self.assertEqual(registered['acceptedLevel'],'L3')
        def takeover():
            return self.call('call',tool='agentbridge_host_coordinator_lease_acquire',hostInstanceId='fixture-takeover',
                             arguments={'task_id':task,'takeover':True,'expected_version':current['version']})['result']
        self.assertTrue(takeover().get('isError'))
        self.query("UPDATE agent_task_coordinator_leases SET expires_at='2000-01-01T00:00:00+00:00' WHERE task_id=?",task)
        new=takeover()['coordinatorLease']
        self.assertEqual(new['version'],current['version']+1)
        self.activate()
        old_meta={'io.agentbridge/task':{'taskId':task,'coordinatorLeaseVersion':current['version']}}
        refused=self.call('call',tool='agentbridge_interaction_resume',arguments={'interaction_id':record['interactionId']},meta=old_meta)['result']
        self.assertTrue(refused.get('isError'),refused)
        self.assertEqual(self.query('SELECT * FROM fixture_calls'),[])
        new_meta={'io.agentbridge/task':{'taskId':task,'coordinatorLeaseVersion':new['version']}}
        self.call('call',tool='agentbridge_interaction_resume',hostInstanceId='fixture-takeover',
                  arguments={'interaction_id':record['interactionId']},meta=new_meta)
        self.assertEqual(self.query('SELECT status FROM agent_tasks')[0]['status'],'succeeded')

    def test_explicit_idempotency_deduplicates_and_rejects_changed_parameters(self):
        self.activate()
        args={'limit':3,'idempotency_key':'fixture-explicit-key'}
        first=self.call('call',tool='oa_workflow_pending_list',arguments=args)['result']
        second=self.call('call',tool='oa_workflow_pending_list',arguments=args)['result']
        self.assertEqual(first['operationId'],second['operationId'])
        self.assertTrue(second['reused'])
        conflict=self.call('call',tool='oa_workflow_pending_list',arguments={**args,'limit':4})['result']
        self.assertTrue(conflict.get('isError'),conflict)
        self.activate('bob')
        self.call('call',user='bob',tool='oa_workflow_pending_list',arguments=args)
        self.assertEqual(len(self.query('SELECT * FROM fixture_calls')),2)

    def test_result_unknown_does_not_replay_consumed_authorization(self):
        self.activate()
        task=self.call('call',tool='agentbridge_host_task_ensure',arguments={
            'agent_host':'openclaw','host_task_key':'fixture-write','endpoint_key':'workspace:alice',
            'client_type':'web','external_subject':'alice','conversation_ref':'fixture-write','title':'Synthetic write'})['result']['task']
        record=self.server.send({'action':'authorize_synthetic_write','user':'alice','taskId':task['taskId']})['result']
        self.call('resume',**record)
        self.assertEqual(self.query('SELECT status FROM agent_tasks')[0]['status'],'outcome_unknown')
        self.assertEqual(self.query('SELECT state FROM write_authorizations')[0]['state'],'consumed')
        port=self.connection['port']; self.server.close(); self.start_server(port)
        self.host.close(); self.start_host()
        self.call('resume',**record)
        self.assertEqual(len(self.query("SELECT * FROM fixture_calls WHERE capability='fixture.write.boundary'")),1)
        self.assertEqual(self.query('SELECT status FROM agent_tasks')[0]['status'],'outcome_unknown')

    def test_crash_during_resumed_read_converges_without_automatic_requery(self):
        first=self.call('read',tool='oa_workflow_pending_list',callId='crash-read',arguments={'limit':6})
        record=first['records'][0]; self.activate()
        self.server.send({'action':'arm_read_crash'})
        try:
            self.call('resume',**record)
        except AssertionError:
            pass  # The real MCP transport may reject or coordinator may record the failure.
        self.assertEqual(self.server.process.wait(timeout=10),73)
        port=self.connection['port']; self.server.close(); self.start_server(port)
        self.host.close(); self.start_host()
        self.call('resume',**record)
        self.assertEqual(self.query('SELECT status FROM agent_tasks')[0]['status'],'failed')
        self.assertEqual(len(self.query('SELECT * FROM fixture_calls')),1)
        self.assertIn('LOGIN_READ_INTERRUPTED',json.dumps(self.query('SELECT * FROM user_timeline')))


if __name__=='__main__': unittest.main()

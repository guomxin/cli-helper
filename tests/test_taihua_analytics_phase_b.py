"""Old online routes are retired; query/file isolation lives in test_database_multisource."""
from contextlib import closing
import json
import sqlite3
import tempfile
import unittest
from starlette.testclient import TestClient
from bscli.core.central_service import CentralCapabilityService
from bscli.core.mcp_identities import McpIdentityTokenStore
from bscli.mcp.central import create_central_mcp_server,validate_central_mcp_server_config

class RetirementTests(unittest.TestCase):
    def test_existing_token_authenticates_but_all_old_tools_are_absent_and_denied(self):
        with tempfile.TemporaryDirectory() as root:
            service=CentralCapabilityService(home=root,base_url='http://127.0.0.1:1')
            identities=McpIdentityTokenStore(service.db_path)
            identity=identities.issue(user_subject='a',expected_principal_ref='unused',scopes=['taihua:read'])
            with closing(sqlite3.connect(service.db_path)) as c,c:
                c.execute('UPDATE mcp_identity_tokens SET scopes_json=? WHERE token_id=?',
                    (json.dumps(['taihua:read','taihua:analytics:read','taihua:analytics:export']),identity['token_id']))
            self.assertIsNotNone(identities.verify(identity['token']))
            server=create_central_mcp_server(service=service,identity_store=identities,
                config=validate_central_mcp_server_config(host='127.0.0.1',port=8790,public_base_url='http://testserver',tls_cert=None,tls_key=None),
                auth_card_base_url='http://127.0.0.1:8780')
            headers={'Accept':'application/json, text/event-stream','Authorization':'Bearer '+identity['token'],'MCP-Protocol-Version':'2025-06-18'}
            with TestClient(server.streamable_http_app()) as client:
                def request(method,params):
                    return client.post('/mcp',headers=headers,json={'jsonrpc':'2.0','id':'test','method':method,'params':params}).json()['result']
                names={x['name'] for x in request('tools/list',{})['tools']}
                self.assertFalse(any(n.startswith('taihua_analytics_') for n in names))
                self.assertIn('taihua_work_log_my_list',names)
                for name in ('personal_summary','result_get','report_export','report_download'):
                    self.assertTrue(request('tools/call',{'name':'taihua_analytics_'+name,'arguments':{}})['isError'])
                result=request('tools/call',{'name':'database_execute','arguments':{'source_id':'taihua_primary','capability':'database.free.read','arguments':{'sql':'select 1'}}})['structuredContent']
                self.assertEqual(result['code'],'DATABASE_CAPABILITY_DENIED')
            catalog=service.planning_catalog(granted_scopes=['taihua:read','taihua:analytics:read','taihua:analytics:export'])
            self.assertNotIn('taihua.analytics.personal.summary',json.dumps(catalog))
            self.assertNotIn('taihua_personal_compare',json.dumps(catalog))

    def test_old_workspace_download_does_not_load_old_payload(self):
        from unittest.mock import MagicMock
        from bscli.workspace.application import WorkspaceApplication,WorkspaceArtifactError
        obj=MagicMock()
        with self.assertRaises(WorkspaceArtifactError):
            WorkspaceApplication.analytics_report(obj,{'user_subject':'a'},'a'*32)
        obj.service.analytics_runtime.assert_not_called()

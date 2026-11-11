"""No-model timing, privacy and official-config regressions."""
import json,tempfile,unittest,urllib.request
from pathlib import Path
from official_codex_observer import TimingObserver,normalize
from agent_operation_timeline import extract


def attr(k,v):return {'key':k,'value':{'stringValue':v}}

class ObservationTests(unittest.TestCase):
    def test_metadata_only(self):
        secret='PRIVATE_PROMPT_AUTH_SECRET'
        p={'resourceLogs':[{'scopeLogs':[{'logRecords':[{'timeUnixNano':'1000000000',
           'body':{'stringValue':secret},'attributes':[attr('event.name','codex.api_request'),
           attr('Authorization',secret),attr('prompt',secret),attr('tool.arguments',secret),
           {'key':'duration_ms','value':{'intValue':'123'}}]}]}]}]}
        result=normalize('/v1/logs',p)
        self.assertNotIn(secret,json.dumps(result))
        self.assertEqual(result[0]['attributes']['duration_ms'],123)
        self.assertEqual(result[0]['event'],'codex.api_request')

    def test_server_metric_is_explicit_not_inferred_generation_time(self):
        p={'resourceMetrics':[{'scopeMetrics':[{'metrics':[{'name':'codex.responses_api_inference_time.duration_ms',
           'histogram':{'dataPoints':[{'count':'2','sum':1250,'timeUnixNano':'999'}]}}]}]}]}
        rows=normalize('/v1/metrics',p)
        self.assertEqual(rows[0]['sum'],1250)
        self.assertEqual(rows[0]['count'],2)
        self.assertNotIn('response_id',rows[0])  # An aggregate cannot be assigned to a request.

    def test_http_and_config_are_passive(self):
        with tempfile.TemporaryDirectory() as tmp:
            obs=TimingObserver(Path(tmp)/'observation')
            try:
                payload={'resourceLogs':[{'scopeLogs':[{'logRecords':[{'body':{'stringValue':'codex.websocket_event'},
                         'attributes':[attr('kind','response.created')]}]}]}]}
                port=obs.server.server_address[1]
                req=urllib.request.Request('http://127.0.0.1:'+str(port)+'/v1/logs',data=json.dumps(payload).encode(),headers={'Content-Type':'application/json'})
                with urllib.request.urlopen(req) as reply:self.assertEqual(reply.status,200)
                self.assertEqual(obs.snapshot()['records'],1)
                original=['codex','exec','--model','frozen-model','-']
                updated=obs.command(original)
                self.assertEqual(updated[:4],original[:4]);self.assertEqual(updated[-1],'-')
                self.assertIn('otel.log_user_prompt=false',updated)
                with self.assertRaises(ValueError):obs.command(['codex','exec'])
            finally:obs.close();obs.close()
            self.assertEqual(len((Path(tmp)/'observation/events.jsonl').read_text().splitlines()),1)

    def test_background_check_is_not_counted_as_silent_model_time(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);results=root/'results';home=root/'home';sandbox=root/'sandbox'
            results.mkdir();(home/'sessions').mkdir(parents=True);(sandbox/'.vero-attempts/x').mkdir(parents=True)
            (results/'final-summary.json').write_text(json.dumps({'clock':{'started_unix':0,'wall_seconds':400}}))
            rows=[{'timestamp':'1970-01-01T00:00:01Z','type':'token_usage_record','payload':{'response_id':'r1'}},
              {'timestamp':'1970-01-01T00:00:02Z','type':'response_item','payload':{'type':'custom_tool_call','call_id':'t','name':'exec'}},
              {'timestamp':'1970-01-01T00:00:03Z','type':'response_item','payload':{'type':'custom_tool_call_output','call_id':'t'}},
              {'timestamp':'1970-01-01T00:05:50Z','type':'response_item','payload':{'type':'reasoning','id':'item'}},
              {'timestamp':'1970-01-01T00:05:56Z','type':'token_usage_record','payload':{'response_id':'r2'}}]
            (home/'sessions/test.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
            (sandbox/'.vero-attempts/x/submitted.json').write_text(json.dumps({'started_at':2}))
            (sandbox/'.vero-attempts/x/result.json').write_text(json.dumps({'ended_at':100,'status':'ok'}))
            result=extract(results,home,sandbox)
            long=[v for v in result['operation_silences'] if v['seconds']>180]
            self.assertEqual(len(long),1);self.assertEqual(long[0]['seconds'],250)
            self.assertIsNone(long[0]['server_inference_seconds'])
            self.assertEqual(long[0]['previous_event']['kind'],'native.completed')

if __name__=='__main__':unittest.main()

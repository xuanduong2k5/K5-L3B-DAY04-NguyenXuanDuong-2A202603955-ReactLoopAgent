"""Kiểm tra agent với tools bonus mà không gọi LLM API."""
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from agent import run_agent
from bonus.tools import BonusTools


def call(call_id,name,args):
    raw=json.dumps(args)
    c={'id':call_id,'name':name,'arguments':raw}
    m={'role':'assistant','content':None,'tool_calls':[{'id':call_id,'type':'function','function':{'name':name,'arguments':raw}}]}
    return {'message':m,'calls':[c],'finish':'tool_calls','usage':{}}

def final(answer='Đã kiểm tra'):
    return {'message':{'role':'assistant','content':answer},'calls':[],'finish':'stop','usage':{}}


class ScriptedProvider:
    def __init__(self,responses): self.responses=iter(responses); self.histories=[]; self.schemas=[]
    def complete(self,messages,schemas):
        self.histories.append(list(messages));self.schemas.append(schemas)
        return next(self.responses)


class BonusAgentTests(unittest.TestCase):
    def test_bonus_schemas_and_system_prompt(self):
        t=BonusTools();p=ScriptedProvider([final()])
        self.assertEqual(run_agent('Kiểm tra',p,t)['status'],'completed')
        names={x['function']['name'] for x in p.schemas[0]}
        self.assertTrue({'get_github_status','get_cloudflare_status','get_vercel_status','get_npm_status','get_digitalocean_status'}.issubset(names))
        self.assertIn('DigitalOcean',p.histories[0][0]['content'])
    def test_429_observation_returns_to_model(self):
        t=BonusTools(scenario='rate_limit_once')
        p=ScriptedProvider([call('a','get_vercel_status',{}),call('b','get_vercel_status',{}),final()])
        result=run_agent('Kiểm tra',p,t,max_steps=5)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(json.loads(p.histories[1][-1]['content'])['error'],'RATE_LIMITED')
        self.assertEqual(json.loads(p.histories[2][-1]['content'])['provider'],'vercel')

    def test_final_answer_is_deferred_to_retry_rate_limited_tool(self):
        t=BonusTools(scenario='rate_limit_once')
        first=call('limited','get_vercel_status',{})
        final_response=final('Chưa thể xác nhận.')
        p=ScriptedProvider([
            first,
            final_response,
            call('retry','get_vercel_status',{}),
            final('Vercel operational. https://www.vercel-status.com'),
        ])
        result=run_agent('Kiểm tra status Vercel.',p,t,max_steps=5)

        self.assertEqual(result['status'],'completed')
        self.assertTrue(any(x.get('event')=='retry_requested' for x in result['trace']))
        observation=next(m for m in p.histories[2] if m.get('tool_call_id')=='limited')
        self.assertEqual(json.loads(observation['content'])['error'],'RATE_LIMITED')
        self.assertEqual(
            sum(x.get('event')=='tool' and x.get('name')=='get_vercel_status' for x in result['trace']),
            2,
        )
    def test_invalid_arguments_observation_returns_to_model(self):
        t=BonusTools()
        bad={'provider':'npm','incident_id':'cc0t2r4403sy','offset':'zero'}
        good={'provider':'npm','incident_id':'cc0t2r4403sy','offset':0}
        details=t.execute('get_incident_details',good)
        p=ScriptedProvider([call('a','get_incident_details',bad),call('b','get_incident_details',good),final('Nguồn: '+details['url'])])
        result=run_agent('Kiểm tra',p,t,max_steps=5)
        self.assertEqual(result['status'],'completed')
        self.assertEqual(json.loads(p.histories[1][-1]['content'])['error'],'INVALID_ARGUMENTS')
        self.assertTrue(json.loads(p.histories[2][-1]['content'])['ok'])

    def test_persistent_rate_limit_is_bounded_and_reported(self):
        t=BonusTools(scenario='rate_limit_always')
        responses=[call(str(i),'get_vercel_status',{}) for i in range(4)]+[final()]
        p=ScriptedProvider(responses)
        result=run_agent('Kiểm tra Vercel',p,t,max_steps=6)

        self.assertEqual(result['status'],'completed')
        tool_events=[x for x in result['trace'] if x.get('event')=='tool' and x.get('name')=='get_vercel_status']
        self.assertEqual(len(tool_events),3)
        self.assertEqual(len([x for x in result['trace'] if x.get('event')=='retry_blocked']),1)
        last_observation=next(
            m for m in p.histories[-1]
            if m.get('role')=='tool' and m.get('tool_call_id')=='3'
        )
        self.assertEqual(json.loads(last_observation['content'])['error'],'RETRY_LIMIT_EXCEEDED')

    def test_retry_after_over_budget_stops_without_another_execution(self):
        class LongRetryTools(BonusTools):
            def __init__(self):
                super().__init__()
                self.executions=0
            def execute(self,name,args):
                self.executions+=1
                return {'ok':False,'error':'RATE_LIMITED','retry_after':'30','retriable':True}

        t=LongRetryTools()
        p=ScriptedProvider([
            call('first','get_vercel_status',{}),
            call('second','get_vercel_status',{}),
            final('Chưa thể xác nhận trạng thái Vercel do rate limit.'),
        ])
        result=run_agent('Kiểm tra Vercel',p,t,max_steps=4)

        self.assertEqual(result['status'],'completed')
        self.assertEqual(t.executions,1)
        observation=next(
            m for m in p.histories[2]
            if m.get('role')=='tool' and m.get('tool_call_id')=='second'
        )
        self.assertEqual(json.loads(observation['content'])['error'],'RETRY_LIMIT_EXCEEDED')

    def test_retry_after_within_budget_is_respected(self):
        class ShortRetryTools(BonusTools):
            def __init__(self):
                super().__init__()
                self.executions=0
            def execute(self,name,args):
                self.executions+=1
                if self.executions==1:
                    return {'ok':False,'error':'RATE_LIMITED','retry_after_seconds':1}
                return {'provider':'vercel','source':'https://www.vercel-status.com'}

        t=ShortRetryTools()
        p=ScriptedProvider([
            call('first','get_vercel_status',{}),
            call('second','get_vercel_status',{}),
            final(),
        ])
        with patch('agent.time.sleep') as sleep:
            result=run_agent('Kiểm tra Vercel',p,t,max_steps=4)

        self.assertEqual(result['status'],'completed')
        sleep.assert_called_once_with(1)
        self.assertEqual(t.executions,2)

    def test_noisy_tool_result_is_compacted_only_for_model_context(self):
        t=BonusTools(scenario='noisy')
        p=ScriptedProvider([call('noise','get_vercel_status',{}),final()])
        result=run_agent('Kiểm tra Vercel',p,t)

        self.assertEqual(result['status'],'completed')
        model_result=json.loads(p.histories[1][-1]['content'])
        self.assertNotIn('distractors',model_result)
        self.assertEqual(model_result['distractors_omitted'],160)
        trace_result=next(x['result'] for x in result['trace'] if x.get('event')=='tool')
        self.assertEqual(len(trace_result['distractors']),160)

    def test_timeline_next_offset_is_available_to_next_model_step(self):
        t=BonusTools()
        incident_id='sps6n39gm4sg'
        source=t.get_incident_details('vercel',incident_id)['url']
        p=ScriptedProvider([
            call('page-1','get_incident_details',{'provider':'vercel','incident_id':incident_id,'offset':0,'limit':2}),
            call('page-2','get_incident_details',{'provider':'vercel','incident_id':incident_id,'offset':2,'limit':2}),
            final('Đã đọc các trang cần thiết. Nguồn: '+source),
        ])
        result=run_agent('Tìm update ở trang sau của incident.',p,t,max_steps=4)

        self.assertEqual(result['status'],'completed')
        first_page=json.loads(p.histories[1][-1]['content'])
        second_page=json.loads(p.histories[2][-1]['content'])
        self.assertEqual(first_page['next_offset'],2)
        self.assertEqual(second_page['offset'],2)

    def test_scope_question_requires_incident_detail_evidence(self):
        t=BonusTools(scenario='noisy')
        vercel_status=t.status('vercel','Builds')
        npm_status=t.status('npm','publish')
        incident_calls=[
            call(
                'vercel-detail',
                'get_incident_details',
                {'provider':'vercel','incident_id':vercel_status['incidents'][0]['id']},
            ),
            call(
                'npm-detail',
                'get_incident_details',
                {'provider':'npm','incident_id':npm_status['incidents'][0]['id']},
            ),
        ]
        combined=incident_calls[0]
        combined['calls'].extend(incident_calls[1]['calls'])
        combined['message']['tool_calls'].extend(incident_calls[1]['message']['tool_calls'])
        vercel_url=t.get_incident_details('vercel',vercel_status['incidents'][0]['id'])['url']
        npm_url=t.get_incident_details('npm',npm_status['incidents'][0]['id'])['url']
        p=ScriptedProvider([
            call('vercel-status','get_vercel_status',{'component':'Builds'}),
            call('npm-status','get_npm_status',{'component':'publish'}),
            final('Status đã kiểm tra.'),
            combined,
            final('Phạm vi incident theo timeline. '+vercel_url+' '+npm_url),
        ])
        result=run_agent(
            'Đối chiếu incident mới nhất của Vercel Builds và npm publishing; nêu phạm vi, timestamp và nguồn.',
            p,
            t,
            max_steps=6,
        )

        self.assertEqual(result['status'],'completed')
        self.assertTrue(any(x.get('event')=='validation_missing_incident_details' for x in result['trace']))
        detail_events=[x for x in result['trace'] if x.get('event')=='tool' and x.get('name')=='get_incident_details']
        self.assertEqual(len(detail_events),2)

if __name__=='__main__': unittest.main()

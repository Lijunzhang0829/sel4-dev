from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from regen.execution.proof_commits import ProofCommits, durable, digest
from regen.execution.repository_execution import validator, context_graph, revoke_graph
from regen.execution.fact_context import manifest
from regen.planning.proof_graph import build_graph


def fixture(contracts=True):
    package={'theory':'Generated_Demo','file_slot':'Generated_Demo.thy','repository_projection':{'nodes':[]},
        'contracts':[{'contract_id':'contract','name':'target','exact_statement':'lemma target: "True"','contract_owned_context':[]}] if contracts else []}
    proposal={'generated_nodes':[{'id':'helper','name':'helper','role':'helper','statement':'lemma helper: "True & True"','context':[],'dependencies':[],'strategy':''}],
        'contract_dependencies':{}}
    graph=context_graph(build_graph(package,proposal,run_id='r',revision=1),[])
    state={'graph':graph,'proofs':{},'accepted_order':[],'rounds':{},'revisions':{},'status':'planned','seal_id':None}
    return package,state

def check():
    import hashlib
    source='lemma helper: "True & True" lemma target: "True" by simp'
    return {'verdict':'green','returncode':0,'source_content':source,'log_content':'',
        'source_sha256':hashlib.sha256(source.encode()).hexdigest(),'log_sha256':hashlib.sha256(b'').hexdigest()}

class ProofCommitsTest(unittest.TestCase):
    def test_commit_before_pointer_crash_and_corrupt_evidence(self):
        p,s=fixture()
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);j=ProofCommits(d,{'run':'r'},validator(p))
            original=durable
            def interrupted(path,*args,**kwargs):
                if path.name=='HEAD.json': raise OSError('crash after commit')
                return original(path,*args,**kwargs)
            with patch('regen.execution.proof_commits.durable',side_effect=interrupted):
                with self.assertRaises(OSError): j.append('initial',s,{'check':check()})
            self.assertEqual(j.replay(),s)
            self.assertTrue((d/'HEAD.json').exists())
            path=d/'commits/000001.json';record=json.loads(path.read_text());record['evidence']={};path.write_text(json.dumps(record))
            with self.assertRaises(ValueError): j.replay()

    def test_uncommitted_candidate_and_missing_commit(self):
        p,s=fixture()
        with tempfile.TemporaryDirectory() as temp:
            d=Path(temp);durable(d/'candidate.json',s)
            j=ProofCommits(d,{},validator(p));self.assertIsNone(j.replay())
            j.append('initial',s,{'check':check()})
            new=deepcopy(s);new['rounds']['helper']=1
            j.append('attempt',new,{})
            (d/'commits/000001.json').unlink()
            with self.assertRaises(ValueError):j.replay()

    def test_green_binding_and_immutable_prefix(self):
        p,s=fixture();v=validator(p)
        new=deepcopy(s);new['proofs']={'helper':'by simp'};new['accepted_order']=['helper'];new['graph']=context_graph(s['graph'],['helper'])
        evidence={'node_id':'helper','check':check(),'sealable_generation':True,'visibility':manifest(s['graph'],{},'helper')}
        v(s,new,'green',evidence)
        bad=deepcopy(evidence);bad['visibility']['context_id']='wrong'
        with self.assertRaises(ValueError):v(s,new,'green',bad)
        revised=deepcopy(new);revised['proofs']['helper']='by blast'
        with self.assertRaises(ValueError):v(new,revised,'revision',{'check':check()})
        with self.assertRaises(ValueError):revoke_graph(p,new['graph'],'helper')

    def test_revoke_keeps_contract_and_cumulative_cost(self):
        p,s=fixture();s['rounds']['helper']=3
        new=deepcopy(s);new['graph']=revoke_graph(p,s['graph'],'helper')
        validator(p)(s,new,'revoke',{'check':check(),'reason':'terminal failure'})
        self.assertEqual([n['id'] for n in new['graph']['nodes']],['contract'])
        bad=deepcopy(new);bad['rounds']={}
        with self.assertRaises(ValueError):validator(p)(s,bad,'revoke',{'check':check(),'reason':'failure'})
        with self.assertRaises(ValueError):revoke_graph(p,s['graph'],'contract')

    def test_auxiliary_only_retires_and_split_cannot_remove_parent(self):
        p,s=fixture(False);new=deepcopy(s);new['graph']=revoke_graph(p,s['graph'],'helper');new['status']='retired'
        validator(p)(s,new,'revoke',{'check':check(),'reason':'terminal failure'})
        with self.assertRaises(ValueError):validator(p)(s,new,'revision',{'check':check(),'decision':{'operation':'SPLIT'},'node_id':'helper'})

    def test_rejected_split_does_not_turn_revocation_into_split(self):
        p,s=fixture();new=deepcopy(s);new['graph']=revoke_graph(p,s['graph'],'helper')
        evidence={'check':check(),'reason':'failed candidate; revoke auxiliary',
                  'decision':{'operation':'DELETE'},'preceding_gra_decision':{'operation':'SPLIT'},'node_id':'helper'}
        validator(p)(s,new,'revoke',evidence)
        evidence['decision']={'operation':'SPLIT'}
        with self.assertRaises(ValueError):validator(p)(s,new,'revision',evidence)

    def test_unconsumed_nonleaf_and_fixed_contract_coverage(self):
        p,s=fixture();proposal={'generated_nodes':[{'id':'a','name':'a','role':'helper','statement':'lemma a: "True & True"','dependencies':[]}, {'id':'b','name':'b','role':'helper','statement':'lemma b: "True | True"','dependencies':['a']} ]}
        graph=build_graph(p,proposal,run_id='r',revision=1)
        self.assertEqual(graph['execution_order'][:2],['a','b'])

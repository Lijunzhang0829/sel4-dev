"""Adversarial evidence-binding tests using explicitly fake native transport.

These test metadata validation, not the truth of synthetic encoded terms.
The real Calg parameter-instance audit is run separately during preparation.
"""
from copy import deepcopy
import hashlib
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from regen.execution.contract_refutation import OWNED_CONTEXT_SUPPORT
from regen.execution.proof_commits import digest
from regen.execution.result_scope import environment_bundle, validate_environment_bundle
from regen.preparation import calg_environment_evidence as audit_module
from regen.tests.current.test_result_scope import synthetic_scope


class AuditTransport:
    def __init__(self, root, raw, *, returncode=0, changed_source=False):
        self.root,self.raw,self.returncode,self.changed_source=root,raw,returncode,changed_source
        self.calls=0
    def imports(self):return ['Main']
    def _check(self,source,label):
        self.calls+=1
        path=self.root/(label+'.thy');path.write_text(source+(' changed' if self.changed_source else ''))
        log=path.with_suffix('.log');log.write_text(self.raw)
        return {'source':str(path),'log':str(log),'verdict':'green' if self.returncode==0 else 'red',
                'returncode':self.returncode}


class CalgEnvironmentEvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.contracts=[{'contract_id':'fixed','exact_statement':'lemma fixed: "False"',
                         'contract_owned_context':[]}]
        self.scope=synthetic_scope()
        self.identity=self.scope['environment_term_sha256']
        self.scan={'passed':True,'records':{'fixed':self.scope},
            'support_sha256':hashlib.sha256(OWNED_CONTEXT_SUPPORT.read_bytes()).hexdigest()}

    def fields(self):
        return {'source_term':self.scope['environment_term'],'unfolded_term':'00',
            'rewrite_proposition':'01','instance_term':'02','proved_term':'02',
            'skip_proof':'false','oracles':'0','hypotheses':'0','changed_axioms':'false',
            'original_constants_established':'false'}

    def xml(self, fields=None):
        xml=ET.Element('parameter_instance',environment_sha256=self.identity)
        for key,value in (self.fields() if fields is None else fields).items():
            ET.SubElement(xml,key).text=value
        return xml

    @staticmethod
    def log(xml):
        return 'REGEN_ENVIRONMENT_INSTANCE '+ET.tostring(xml,encoding='unicode').encode().hex()+'\n'

    def evidence(self):
        value={'passed':True,'records':{self.identity:self.fields()},
            'verifier_sha256':hashlib.sha256(Path(audit_module.__file__).read_bytes()).hexdigest(),
            'native_check':{'verdict':'green','returncode':0},'model_calls':0}
        return self.rehash(value)

    @staticmethod
    def rehash(value):
        value['evidence_sha256']=digest({k:v for k,v in value.items() if k!='evidence_sha256'})
        return value

    def test_complete_transport_retains_rewrite_and_does_not_establish_original_constants(self):
        checker=AuditTransport(self.root,self.log(self.xml()))
        result=audit_module.check_parameter_instances(checker,self.scan,self.root/'ok')
        self.assertTrue(result['passed'])
        self.assertEqual(result['records'][self.identity]['rewrite_proposition'],'01')
        bundle=environment_bundle(self.contracts,self.scan,'checker:test',parameter_instance_evidence=result)
        row=bundle['contracts']['fixed']
        self.assertEqual(row['fixed_environment_status'],'not_established')
        self.assertFalse(row['parameter_instance_evidence']['establishes_original_constants'])
        self.assertEqual(result['evidence_sha256'],row['parameter_instance_evidence']['evidence_sha256'])

    def test_kernel_exit_without_all_audits_and_failed_native_exit_never_pass(self):
        for index,(log,code) in enumerate([('',0),(self.log(self.xml()),1)]):
            checker=AuditTransport(self.root,log,returncode=code)
            result=audit_module.check_parameter_instances(checker,self.scan,self.root/str(index))
            self.assertFalse(result['passed'])
            with self.assertRaises(ValueError):
                environment_bundle(self.contracts,self.scan,'checker:test',parameter_instance_evidence=result)

    def test_native_xml_rejects_scope_rebinding_unsafe_derivation_and_ambiguous_fields(self):
        variants=[]
        for key,value in [('source_term','deadbeef'),('proved_term','03'),('rewrite_proposition',''),
                ('skip_proof','true'),('oracles','1'),('hypotheses','1'),('changed_axioms','true'),
                ('original_constants_established','true')]:
            fields=self.fields();fields[key]=value;variants.append(self.xml(fields))
        duplicate=self.xml();ET.SubElement(duplicate,'oracles').text='0';variants.append(duplicate)
        wrong=self.xml();wrong.set('environment_sha256','0'*64);variants.append(wrong)
        for index,xml in enumerate(variants):
            with self.subTest(index=index),self.assertRaises(ValueError):
                audit_module.check_parameter_instances(AuditTransport(self.root,self.log(xml)),
                    self.scan,self.root/str(index))

    def test_duplicate_environment_or_changed_executed_source_is_rejected(self):
        for index,checker in enumerate([AuditTransport(self.root,self.log(self.xml())*2),
                AuditTransport(self.root,self.log(self.xml()),changed_source=True)]):
            with self.subTest(index=index),self.assertRaises(ValueError):
                audit_module.check_parameter_instances(checker,self.scan,self.root/str(index))

    def test_bundle_rechecks_producer_digest_and_native_success(self):
        variants=[]
        changed=self.evidence();changed['records'][self.identity]['source_term']='00';variants.append(changed)
        for mode in ('passed','verdict','returncode','verifier','source','rewrite','oracle','proved'):
            changed=self.evidence();row=changed['records'][self.identity]
            if mode=='passed':changed['passed']=False
            elif mode=='verdict':changed['native_check']['verdict']='red'
            elif mode=='returncode':changed['native_check']['returncode']=1
            elif mode=='verifier':changed['verifier_sha256']='0'*64
            elif mode=='source':row['source_term']='00'
            elif mode=='rewrite':row.pop('rewrite_proposition')
            elif mode=='oracle':row['oracles']='1'
            else:row['proved_term']='ff'
            variants.append(self.rehash(changed))
        for index,value in enumerate(variants):
            with self.subTest(index=index),self.assertRaises(ValueError):
                environment_bundle(self.contracts,self.scan,'checker:test',parameter_instance_evidence=value)

    def test_instance_summary_cannot_add_authority_or_invalid_hashes(self):
        original=environment_bundle(self.contracts,self.scan,'checker:test',parameter_instance_evidence=self.evidence())
        for key,value in [('instance_term_sha256','not-a-hash'),('evidence_sha256',''),
                          ('establishes_original_constants',True),('unexpected_authority','proved')]:
            altered=deepcopy(original);altered['contracts']['fixed']['parameter_instance_evidence'][key]=value
            altered['bundle_sha256']=digest({k:v for k,v in altered.items() if k!='bundle_sha256'})
            with self.subTest(key=key),self.assertRaises(ValueError):
                validate_environment_bundle(altered,self.contracts,checker_context_id='checker:test')


if __name__=='__main__':unittest.main()

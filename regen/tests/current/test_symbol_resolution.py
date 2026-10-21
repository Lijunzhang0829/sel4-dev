from pathlib import Path
import tempfile
import unittest
from types import SimpleNamespace
from regen.execution.symbol_resolution import resolve_symbols, TERM_LOOKUP_NOTICE


def encoded(tag, *fields):
    return '\t'.join([tag, *(field.encode().hex() for field in fields)])+'\n'


class SymbolResolutionTest(unittest.TestCase):
    def checker(self, directory, output, verdict='green'):
        checker=SimpleNamespace(work_dir=Path(directory),timeout=900,imports=lambda:['Base'])
        def check(source,label,**kwargs):
            checker.calls=getattr(checker,'calls',0)+1
            checker.source=source
            checker.active_timeout=kwargs.get('timeout',checker.timeout)
            self.assertEqual(checker.timeout,900)
            path=kwargs['writable_mounts'][0][0]/'resolution.tsv'
            path.write_text(output)
            return {'verdict':verdict,'diagnostic':'infrastructure failure' if verdict!='green' else ''}
        checker._check=check
        return checker

    def test_mixed_results_and_error_capture_without_interrupt_or_io_masking(self):
        output=(encoded('RESOLVED','True','HOL.True','HOL.bool','HOL.True')+
                encoded('CONSTANT','True','HOL.True','HOL.bool')+
                encoded('UNRESOLVED','prod.case','Undefined constant prod.case'))
        with tempfile.TemporaryDirectory() as tmp:
            checker=self.checker(tmp,output)
            results=resolve_symbols(checker,['True','prod.case'],[])
            self.assertEqual(results['True']['status'],'resolved')
            self.assertEqual(results['prod.case']['status'],'unresolved')
            self.assertEqual(results['True']['constant_names'],['HOL.True'])
            for resolution in results.values():
                self.assertEqual(resolution['lookup_kind'],'term')
                self.assertFalse(resolution['fact_lookup_performed'])
                self.assertEqual(resolution['notice'],TERM_LOOKUP_NOTICE)
            self.assertIn('does not query facts', results['prod.case']['notice'])
            self.assertEqual(checker.timeout,900)
            self.assertEqual(checker.active_timeout,120)
            self.assertIn('Exn.capture',checker.source)
            self.assertIn('if Exn.is_interrupt exn then Exn.reraise exn',checker.source)
            self.assertIn('Runtime.exn_message exn',checker.source)
            self.assertNotIn('row "RESOLVED"',checker.source)
            self.assertIs(resolve_symbols(checker,['True','prod.case'],[]),results)

    def test_full_advertised_batch_uses_one_native_check(self):
        names=['constant_'+str(n) for n in range(16)]
        output=''.join(encoded('RESOLVED',name,'Base.'+name,'HOL.bool','Base.'+name)
                       for name in names)
        with tempfile.TemporaryDirectory() as tmp:
            checker=self.checker(tmp,output)
            results=resolve_symbols(checker,names,[])
            self.assertEqual(list(results),names)
            self.assertEqual(checker.calls,1)
            self.assertIs(resolve_symbols(checker,names,[]),results)
            self.assertEqual(checker.calls,1)

    def test_invalid_requests_do_not_start_native_check(self):
        invalid=[[],['x']*17,['x; ML'],['x'*257],[None],None,'True']
        with tempfile.TemporaryDirectory() as tmp:
            checker=self.checker(tmp,'')
            for names in invalid:
                with self.subTest(names=names):
                    with self.assertRaisesRegex(ValueError,'1-16 plain Isabelle term identifiers'):
                        resolve_symbols(checker,names,[])
            self.assertFalse(hasattr(checker,'calls'))

    def test_native_failure_is_not_cached_as_unresolved(self):
        with tempfile.TemporaryDirectory() as tmp:
            checker=self.checker(tmp,encoded('UNRESOLVED','prod.case','not found'),verdict='red')
            with self.assertRaisesRegex(RuntimeError,'infrastructure failure'):
                resolve_symbols(checker,['prod.case'],[])
            self.assertEqual(checker.timeout,900)
            self.assertEqual(checker._symbol_cache,{})

    def test_incomplete_output_is_infrastructure_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            checker=self.checker(tmp,'')
            with self.assertRaisesRegex(RuntimeError,'Incomplete'):
                resolve_symbols(checker,['True'],[])
            self.assertEqual(checker._symbol_cache,{})

if __name__=='__main__':unittest.main()

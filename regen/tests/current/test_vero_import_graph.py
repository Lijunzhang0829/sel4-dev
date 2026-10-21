"""Direct VERO checker import parsing must reject cyclic source snapshots."""
import unittest

from regen.execution.vero_import_graph import effective_generated_imports


class VeroImportGraphTests(unittest.TestCase):
    def test_direct_rendered_snapshot_rejects_additive_cycle(self):
        def source(name, other):
            return (f'theory {name}\nimports Vero_Support\n'
                    f'(* VERO START {name} imports *)\n{other}\n'
                    f'(* VERO END {name} imports *)\nbegin\nend\n').encode()

        snapshot={
            'Vero_A.thy':source('Vero_A','Vero_B'),
            'Vero_B.thy':source('Vero_B','Vero_A'),
        }
        with self.assertRaisesRegex(ValueError,'cycle'):
            effective_generated_imports(snapshot,
                {'Vero_A':[], 'Vero_B':[]}, require_rendered=True)


if __name__=='__main__':
    unittest.main()

from copy import deepcopy
import unittest
from regen.infrastructure.workspace_transcript import approved_registry
from regen.planning.generic_codex_agent import registry

class GenericRegistryTests(unittest.TestCase):
    def test_exact_registry_and_no_added_or_modified_authority(self):
        tools=[{'type':'function',**tool} for tool in registry()]
        self.assertTrue(approved_registry(tools))
        self.assertFalse(approved_registry(tools+[{'type':'function','name':'shell','inputSchema':{}}]))
        altered=deepcopy(tools)
        next(t for t in altered if t['name']=='repository_plan')['inputSchema']={}
        self.assertFalse(approved_registry(altered))
        self.assertFalse(approved_registry([t for t in tools if t['name']=='repository_plan']))

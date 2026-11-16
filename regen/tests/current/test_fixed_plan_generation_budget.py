import json
from regen.tests.current.test_planning_budget_handoff import PlanningBudgetHandoffTests
from regen.infrastructure.parallel_generation_budget import initialize_parallel_budget
from regen.execution.proof_commits import digest

class FixedPlanGenerationTests(PlanningBudgetHandoffTests):
    def authorize(self):
        self.write(self.root/'fixed-plan-generation.json',{
            'scope':'fresh-fixed-plan-generation-only-v1',
            'run_sha256':digest(self.run),'schedule_sha256':self.sha(self.list_path),
            'seconds':100,'authorization':'User requests reused plan and 100 seconds fresh generation'})

    def test_explicit_generation_only_starts_without_planning_charge(self):
        self.authorize()
        stage,bridge=initialize_parallel_budget(self.root,self.overview,self.binding,
            seconds=100,clock_mode='experiment-runtime')
        self.stages.append(stage)
        self.assertFalse(stage.snapshot()['include_planning'])
        self.assertLess(stage.snapshot()['effective_seconds'],5)

    def test_changed_plan_rejected(self):
        self.authorize()
        self.list_path.write_text('{}')
        with self.assertRaisesRegex(ValueError,'fixed-plan'):
            initialize_parallel_budget(self.root,self.overview,self.binding,seconds=100,
                clock_mode='experiment-runtime')

    def test_missing_authorization_still_requires_planning(self):
        with self.assertRaisesRegex(ValueError,'planning budget receipt'):
            initialize_parallel_budget(self.root,self.overview,self.binding,seconds=100,
                clock_mode='experiment-runtime')

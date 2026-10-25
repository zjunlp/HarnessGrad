"""Pinned CostBench one-intervention protocol behind the trusted tool relay.

The blocker owns event generation and the independent reference timeline.  This
module supplies the model-independent turn ordering from upstream ``env/run.py``.
Future events and reference plans never enter the public observation.
"""
from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import random

from runtime import CostBenchRuntime


MODES = frozenset(('ban_tool', 'cost_change', 'steplen_change', 'preference_change'))


def _stable_int(text):
    return int(hashlib.sha256(text.encode('utf-8')).hexdigest(), 16) % (2**31 - 1)


class DynamicCostBenchRuntime(CostBenchRuntime):
    def __init__(self, config, public):
        protocol = config['protocol']
        if (protocol.get('use_blocker') is not True or protocol.get('block_mode') not in MODES
                or protocol.get('block_num') != 1 or protocol.get('refinement_level') != 2):
            raise ValueError('unsupported CostBench dynamic protocol')
        super().__init__(config, public)
        from env.settings import load_config
        from env.utils.blocker import create_blocker
        from env.utils.solver import GroundtruthSolver

        defaults = load_config()
        self.blocker = create_blocker(
            block_mode=protocol['block_mode'], block_num=1,
            queries_path=config.get('queries_path', ''), seed_range=defaults.random.seed_range,
            refinement_level=2, output_dir='.')
        self.blocker.set_solver(GroundtruthSolver(tools_with_costs=self.tools))
        self.blocker.filter_options = {
            name: protocol.get(name, getattr(defaults.tool_defaults, name))
            for name in ('control_tool_length', 'max_tool_length', 'ban_longest_tool',
                         'min_atomic_cost', 'max_atomic_cost', 'noise_std')}
        self.query['tool_creation_seed'] = config.get(
            'tool_seed', self.query.get('tool_creation_seed'))
        if not isinstance(self.query['tool_creation_seed'], int):
            raise ValueError('dynamic query needs its pinned per-query tool seed')
        if 'blocking_plan' not in self.query or 'block_stats' not in self.query:
            raise ValueError('dynamic query requires its prepared private blocking plan')
        stats = self.query['block_stats']
        if (len(self.query['blocking_plan']) != 1 or stats.get('expected_block_count') != 1
                or stats.get('block_count') != 0 or len(stats.get('metadata', [])) != 1
                or stats['metadata'][0].get('block_type') != protocol['block_mode']):
            raise ValueError('invalid prepared dynamic blocking plan')
        self.blocker.set_initial_tools(self.tools)
        self.blocker.set_current_state(self.state)
        self.blocker.initialize_gt_state(self.query['task'])
        self.current_query = public['query']
        self.execution_log = []
        self._pending_messages = []
        self._event = None
        self._schema_changed = False

    def show(self):
        """Current model-visible schemas; inspection never consumes a turn."""
        return {'query': self.current_query, 'system_prompt': self.public['system_prompt'],
                'tools': [tool.generate_interface(provide_composite_concept=True)
                          for tool in self.visible.values()]}

    def _refresh_tools(self):
        from env.core.base_types import create_tool_from_dict
        self.tools = {name: create_tool_from_dict({**spec, 'name': name})
                      for name, spec in self.blocker.get_current_tools()['tools'].items()}
        self.visible = {tool.name: tool for tool in self.blocker.get_visible_tools(list(self.tools.values()))
                        if self.query['task'].capitalize() in tool.name}

    def _apply_event(self, name=None):
        before = self.blocker.blocked_count
        # The official solver prints its private plan when recalculating it.
        # Keep that diagnostic out of the actor's protocol and stdout.
        with contextlib.redirect_stdout(io.StringIO()):
            event = self.blocker.execute_blocking(
                self.query, tool_call_request={'tool_name': name} if name is not None else None)
        if (name == '' and event.get('type') == 'ban_tool' and event.get('status') == 'error'
                and self.blocker.blocked_count == before):
            # run.py still emits its normal seeded ban response when the actor
            # supplies an empty name. The blocker records no intervention and
            # the episode remains an ordinary incomplete task, not a service
            # failure. A subsequent nonempty request can trigger the same event.
            self._event = event
            return event
        if event.get('status') in ('error', None) or self.blocker.blocked_count != before + 1:
            raise RuntimeError('CostBench intervention failed')
        scenarios = self.query.get('groundtruth', {}).get('scenarios', [])
        if len(scenarios) != 2 or not scenarios[1].get('gt_id'):
            # Upstream catches recalculation exceptions.  An incomplete reference
            # is an infrastructure failure here, never a fabricated task zero.
            raise RuntimeError('CostBench dynamic reference recalculation failed')
        self._event = event
        self.state = set(self.blocker.current_state)
        if event['type'] in ('cost_change', 'steplen_change'):
            self._refresh_tools()
            self._schema_changed = True
        elif event['type'] == 'preference_change':
            from env.settings import load_config
            location = event.get('new_location_preference') if self.query['task'].lower() != 'location' else ''
            text = (f'This time I want to go to {location}.' if location else '')
            text += event.get('new_user_requirement', '')
            text += ' The previously provided TimeInfo information is still valid.'
            seed = _stable_int(f"{self.query['tool_creation_seed']}:{self.steps}:preference_change_message")
            template = random.Random(seed).choice(load_config().messages.preference_change_user_message_templates)
            message = {'role': 'user', 'content': template.format(new_user_message=text)}
            self._pending_messages.append(message)
            self.current_query = message['content']
        return event

    def _ban_response(self, name, *, first):
        from env.settings import load_config
        tool = self.tools.get(name)
        if first:
            seed = _stable_int(f"{self.query['tool_creation_seed']}:{self.steps}:ban_tool_message")
        else:
            seed = self.query['blocking_plan'][0]['parameters']['return_message_seed']
        text = random.Random(seed).choice(load_config().messages.ban_tool_return_sentences)
        if tool is not None and tool.has_cost():
            text += ' Cost: ' + str(tool.cost) + ('' if first else '.')
        # Unknown names can be selected at the intervention point upstream; the
        # first response then has no cost.  Preserve that observable behavior.
        return text if first or tool is not None and tool.has_cost() else ''

    def _execute(self, name, arguments):
        self.blocker.update_current_step(self.steps)
        ready = self.blocker.should_trigger_blocking_with_query(self.steps, self.query)
        mode = self.config['protocol']['block_mode']
        if ready and mode == 'ban_tool':
            self._apply_event(name)
            return self._ban_response(name, first=True)
        if self.blocker.should_tool_call_fail(name):
            return self._ban_response(name, first=False)
        result = super()._execute(name, arguments)
        self.blocker.set_current_state(self.state)
        if ready and mode != 'ban_tool':
            self._apply_event()
        return result

    def turn(self, content=None, calls=None):
        old_steps = self.steps
        self._pending_messages = []
        self._event = None
        self._schema_changed = False
        result = super().turn(content=content, calls=calls)
        if self.steps == old_steps:
            return result
        self.blocker.update_current_step(self.steps)
        self.history.extend(copy.deepcopy(self._pending_messages))
        entry = {'step': self.steps, 'tool_calls': [c['name'] for c in (calls or [])],
                 'blocking_result': self._event if self._event and self._event['type'] == 'ban_tool' else None,
                 'success': not (self._event and self._event['type'] == 'ban_tool')}
        if self._event and self._event['type'] != 'ban_tool':
            entry['post_blocking_result'] = copy.deepcopy(self._event)
        self.execution_log.append(entry)
        if self._pending_messages:
            result['messages'] = copy.deepcopy(self._pending_messages)
        if self._schema_changed:
            result['tools'] = self.show()['tools']
        # super().turn obtains status after _execute, so preference resets are
        # reflected in goal_reached before returning to the actor.
        return result

    def episode(self):
        payload = super().episode()
        query = copy.deepcopy(self.query)
        query.update(conversation_history=copy.deepcopy(self.history), total_steps=self.steps,
                     status='max_steps_reached' if self.steps >= self.limit else 'completed',
                     is_goal_state=self.final_type in self.state,
                     run_args=dict(self.config['protocol']), execution_log=copy.deepcopy(self.execution_log))
        # Upstream discounts a post-step event when the actor ends immediately
        # afterwards.  Do this on the sealed copy, so episode() remains pure.
        effective = sum(1 for md in query['block_stats']['metadata'][:self.blocker.blocked_count]
                        if md['block_type'] == 'ban_tool' or md['trigger_step'] + 1 < self.steps)
        stats = query['block_stats']
        stats.update(block_count_agent=effective, block_count_gt=effective, block_count=effective)
        status = self.blocker.get_blocking_status()
        status['blocked_count'] = effective
        query['blocking_status'] = status
        query['evaluation'] = {'expected_block_count': 1, 'actual_block_count': effective,
                               'target_scenario_index': effective,
                               'expected_gt': query['groundtruth']['scenarios'][effective].get('gt_id', ''),
                               'actual_result': '', 'success': False}
        payload['query_result'] = query
        return payload

"""Static CostBench environment, separated from the upstream model client.

The candidate owns its planning loop. This service owns tool execution and the
canonical conversation consumed by the pinned official evaluator.
"""
from __future__ import annotations

import copy
import json


class CostBenchRuntime:
    def __init__(self, config, public):
        # bootstrap must establish the pinned package and split before import.
        from env.core.base_types import create_tool_from_dict
        from env.core.data_types import get_final_type
        from env.utils.solver import GroundtruthSolver

        self.config = config
        self.public = public
        self.query = copy.deepcopy(config['query'])
        self.tools = {name: create_tool_from_dict({**spec, 'name': name})
                      for name, spec in config['tools'].items()}
        self.visible = {name: tool for name, tool in self.tools.items()
                        if self.query['task'].capitalize() in name}
        self.state = set(GroundtruthSolver(self.tools)._infer_initial_state(self.query['task']))
        self.final_type = get_final_type(self.query['task'])
        self.history = [{'role': 'system', 'content': public['system_prompt']},
                        {'role': 'user', 'content': public['query']}]
        self.steps = 0
        self.termination = None
        self.limit = config['protocol']['max_tool_steps']

    def status(self):
        return {'steps': self.steps, 'max_steps': self.limit,
                'remaining_steps': self.limit - self.steps,
                'goal_reached': self.final_type in self.state,
                'termination': self.termination}

    def _execute(self, name, arguments):
        tool = self.visible.get(name)
        if tool is None:
            return (f"[ERROR] You have requested an invalid tool '{name}'. Note that you should "
                    'call the exact name of the tool as specified in the tool list.')
        required = set(tool.input_types)
        if not required <= self.state:
            return (f"[ERROR] Missing required input types for tool '{name}'. "
                    f'Required: {sorted(required)}, Current: {sorted(self.state)} Cost: {tool.cost}.')
        try:
            inputs = tool.process_input(arguments)
            output = tool.execute(input_data=inputs, available_tools=self.visible)
            text = tool.process_output(tool_output=output,
                                       refinement_level=self.config['protocol']['refinement_level'],
                                       available_tools=self.visible)
            text += ' Cost: ' + str(tool.cost) if tool.has_cost() else ''
            if 'Cost: ' not in text:
                raise ValueError('tool output contains no cost')
        except (OSError, MemoryError):
            # Filesystem/infrastructure loss is not an ordinary actor failure.
            raise
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            # Same response prefix as upstream _execute_tool_simulation.
            text = f'Tool execution failed: {exc}'
        if not text.startswith(('[ERROR]', 'Tool execution failed', 'Error:')):
            self.state.add(tool.output_type)
        return text if 'Cost:' in text else text + f' Cost: {tool.cost}'

    def turn(self, content=None, calls=None):
        """One official assistant turn, including first-call-only batch semantics."""
        if self.termination is not None:
            return {'error': 'episode_ended', **self.status()}
        calls = [] if calls is None else calls
        if content is not None and not isinstance(content, str):
            return {'error': 'content_must_be_text_or_null'}
        if not isinstance(calls, list) or any(
            not isinstance(c, dict) or set(c) != {'name', 'arguments'}
            or not isinstance(c['name'], str) or not isinstance(c['arguments'], (dict, str))
            for c in calls
        ):
            return {'error': 'calls_must_contain_name_and_arguments'}
        self.steps += 1
        # Match run.py's cleaned JSON schema, not the OpenAI SDK wire objects.
        serialized = [{'id': f'call-{self.steps}-{i}', 'name': c['name'],
                       'arguments': c['arguments'] if isinstance(c['arguments'], str)
                       else json.dumps(c['arguments'])}
                      for i, c in enumerate(calls)]
        self.history.append({'role': 'assistant', 'content': content, 'tool_calls': serialized})
        # Upstream answer-first termination takes precedence over calls only
        # when the enclosed answer contains the configured number of digits.
        answer_first = False
        if content and '<answer>' in content and '</answer>' in content:
            answer = content.split('<answer>')[1].split('</answer>')[0]
            answer_first = sum(c.isdigit() for c in answer) == (7 if self.config['split'] == 'train' else 5)
        results = []
        if answer_first:
            self.termination = 'final_answer'
        elif serialized:
            for i, call in enumerate(serialized):
                text = self._execute(call['name'], call['arguments']) if i == 0 else (
                    '[ERROR] Multiple tool calls in one step are not allowed. Only the first tool call '
                    'was executed. Please call one tool at a time.')
                result = {'role': 'tool', 'tool_call_id': call['id'],
                          'name': call['name'], 'content': text}
                results.append(result)
            self.history.extend(results)
        else:
            self.termination = 'final_answer' if content and '<answer>' in content else 'no_tool_call'
        if self.steps >= self.limit:
            self.termination = self.termination or 'max_steps_reached'
        return {'results': results, **self.status()}

    def episode(self):
        query = copy.deepcopy(self.query)
        query.update(conversation_history=copy.deepcopy(self.history), total_steps=self.steps,
                     status='max_steps_reached' if self.steps >= self.limit else 'completed',
                     is_goal_state=self.final_type in self.state,
                     block_stats={'expected_block_count': 0, 'block_count_agent': 0,
                                  'block_count': 0, 'block_step': []},
                     run_args=dict(self.config['protocol']))
        return {'task_id': self.config['task_id'], 'revision': self.config['revision'],
                'seed': self.config['seed'], 'case_sha256': self.config['case_sha256'],
                'query_result': query, 'termination': self.termination or 'actor_stopped'}

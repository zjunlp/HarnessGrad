"""Track acquired task services/routes/relays; no actor or scoring configuration context."""
from __future__ import annotations

import sys

from eval.service import services, modelgate
from eval.run import tool_relay


class TaskSession:
    def __init__(self, workspace, receipt=None):
        self.workspace = workspace
        self.receipt = receipt if receipt is not None else {}
        self.services = None
        self.network_name = None
        self.owns_network = False
        self.model = None
        self.web = None
        self.stepwise = None
        self.tool = None

    def start_services(self, specs, *, recordings_root, run_id, task_id, run_seed):
        self.services = services.start(specs, recordings_root=recordings_root,
                                      run_id=run_id, task_id=task_id, run_seed=run_seed)
        self.network_name = self.services.network

    def ensure_network(self, *, run_id, task_id):
        if self.network_name is None:
            self.network_name = services.create_network(run_id, task_id)
            self.owns_network = True

    def start_web(self, *, factory, record_path, search, fetch):
        self.web = factory(bind_host=services.gateway_ip(self.network_name), record_path=record_path,
                           enable_search=search, enable_fetch=fetch).start()

    def start_container_model(self, base, *, run_id, task_id):
        self.ensure_network(run_id=run_id, task_id=task_id)
        target, path = modelgate.split(base)
        self.model = modelgate.ModelGateway(bind_host=services.gateway_ip(self.network_name),
                                           target=target, path=path).start()
        return self.model.base_url

    def start_native_model(self, base, *, factory, observe_http):
        self.model = factory(base, **({'observe_http': True} if observe_http else {})).start()

    def prepare_stepwise(self, session, *, factory, contract, public_fields, timeout_s):
        directory = self.workspace.allocate_stepwise()
        self.stepwise = factory(directory, session, contract=contract,
                                public_fields=public_fields, timeout_s=timeout_s)
        return directory

    def start_tool(self, setup, *, context, private_diagnostics):
        self.tool = tool_relay.start(setup, **({'context': context} if context is not None else {}),
                                    **({'private_diagnostics': True} if private_diagnostics else {}))

    def environment_usage(self, *, project_model):
        usage = self.services.usage() if self.services is not None else {}
        if self.tool is not None and self.tool.model is not None:
            usage = project_model(self.tool.model.report())
        return usage

    def model_evidence(self):
        return ({'published': True, 'target': f'{self.model.target[0]}:{self.model.target[1]}',
                 **self.model.account()} if self.model is not None else None)

    def web_evidence(self):
        return ({'published': True, 'url': self.web.url, 'search': self.web.search, 'fetch': self.web.fetch}
                if self.web is not None else None)


    def close(self, output, *, responses_enabled, native_model, task_label):
        """One explicit close pass; receipt mutations remain visible to the caller."""
        output.capture_pending()
        self.collect_responses(responses_enabled)
        self.close_tool(output.evidence)
        self.close_stepwise()
        self.close_model(native_model)
        self.close_web()
        self.close_network(task_label)
        self.close_services(task_label)
        self.workspace.remove_task()
        self.workspace.remove_runtime()

    def collect_responses(self, enabled):
        if enabled():
            from eval.containers.responses_overlay import collect_capture
            self.receipt['responses_capture'] = collect_capture(self.workspace.path)

    def close_tool(self, evidence):
        if self.tool is not None:
            self.tool.cleanup()
            self.receipt['tool_relay'] = self.tool.report()
            if evidence is not None and getattr(self.tool, 'diagnostic', None) is not None:
                evidence['tool_relay'] = self.tool.diagnostic

    def close_stepwise(self):
        if self.stepwise is not None:
            self.stepwise.stop()
        self.workspace.remove_stepwise()

    def close_model(self, native_model):
        if self.model is not None:
            self.model.stop()
            if native_model(self.model) and self.model.http is not None:
                self.receipt['agent_model_http'] = self.model.http.report()

    def close_web(self):
        if self.web is not None:
            self.web.stop()

    def close_network(self, task_label):
        if self.owns_network and self.network_name is not None:
            leaked = services.remove_network(self.network_name)
            if leaked:
                print(f"warning: could not remove the model gateway network for "
                      f"{task_label()}: {leaked}", file=sys.stderr)

    def close_services(self, task_label):
        if self.services is not None:
            leaked = self.services.stop()
            if leaked:
                print(f"warning: could not fully tear down services for "
                      f"{task_label()}: {leaked}", file=sys.stderr)

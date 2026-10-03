"""Real worker/client/adapter/controller HTTP join, with a synthetic broker child."""

import json
import threading
import uuid

import pytest
from agents import mcp, tasks
from agents.hermes import HermesClient
from agents.models import BudgetReservation, DraftOperation, WorkflowCapability
from django.db import close_old_connections

from deploy.hermes import adapter
from deploy.hermes.controller_client import RemoteProcessController
from deploy.hermes.controller_service import ControllerService, Handler
from tests.agents.test_hermes_adapter import Client, make_adapter
from tests.agents.test_hermes_controller_service import FakeController
from tests.agents.test_hermes_tasks import inputs as inputs
from tests.agents.test_hermes_tasks import queue


@pytest.mark.django_db(transaction=True)
def test_worker_http_join_creates_bound_inert_operations(inputs, monkeypatch):
    transport = Client()

    class BrokerChild(FakeController):
        def execute(self, body, **kwargs):
            close_old_connections()
            try:
                binding = transport.events[0][2]

                def call(tool, **extra):
                    return mcp.dispatch_mcp_tool(
                        tool_name=tool,
                        service_identity="synthetic-broker-identity",
                        capability=binding.capability,
                        arguments={
                            "workflow_id": body["workflow_id"],
                            "revision_id": body["revision_id"],
                            "actor_id": body["actor_id"],
                            "correlation_id": body["correlation_id"],
                            "request_id": str(uuid.uuid4()),
                            "idempotency_key": uuid.uuid4().hex,
                            **extra,
                        },
                    )

                proposal = call(
                    "propose_campaign_drafts",
                    proposal={
                        "event_copy": "Synthetic draft",
                        "invitation": {"subject": "Synthetic invitation", "body": "Draft"},
                        "reminder": {"subject": "Synthetic reminder", "body": "Draft"},
                        "social": {"body": "Synthetic social draft"},
                    },
                )
                call("validate_proposal", proposal_id=proposal["proposal_id"])
                call("request_eventbrite_draft", proposal_id=proposal["proposal_id"])
                call("request_iterable_drafts", proposal_id=proposal["proposal_id"])
                return adapter.map_upstream_result(
                    body,
                    {
                        "status": "completed",
                        "output": json.dumps(
                            {
                                "proposal_references": [
                                    {
                                        "proposal_id": proposal["proposal_id"],
                                        "proposal_digest": proposal["proposal_digest"],
                                        "schema_id": tasks.PROPOSAL_SCHEMA_ID,
                                    }
                                ]
                            }
                        ),
                        "usage": {"input_tokens": 100, "output_tokens": 50, "cost_microusd": 200},
                    },
                )
            finally:
                close_old_connections()

    monkeypatch.setattr(mcp, "authenticate_service", lambda identity: None)
    child = BrokerChild()
    controller_token = "synthetic-controller-identity-0000"
    controller = ControllerService(
        ("127.0.0.1", 0),
        Handler,
        service_token=controller_token,
        controller=child,
    )
    controller_thread = threading.Thread(target=controller.serve_forever, daemon=True)
    controller_thread.start()
    server = make_adapter(transport)
    server.process_controller = RemoteProcessController(
        url=f"http://127.0.0.1:{controller.server_port}",
        token=controller_token,
    )
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    client = HermesClient(url=f"http://127.0.0.1:{server.server_port}", token=server.service_token)
    monkeypatch.setattr(HermesClient, "from_settings", lambda: client)
    try:
        run = queue(inputs)
        tasks.execute_hermes_run(str(run.id))
        run.refresh_from_db()
        assert run.status == "succeeded", run.failure_category
        operations = DraftOperation.objects.filter(
            proposal__capability__correlation_id=run.hermes_binding.correlation_id,
        )
        assert operations.count() == 3
        assert set(operations.values_list("status", flat=True)) == {"pending"}
        assert not operations.exclude(approval=None, receipt=None).exists()
        assert WorkflowCapability.objects.get(
            correlation_id=run.hermes_binding.correlation_id
        ).revoked_at
        reservation = BudgetReservation.objects.get(run_id=run.id)
        assert reservation.status == "settled" and reservation.settled_cost_microusd == 200
        assert child.stopped and controller.active is None
        assert transport.events[-1][0] == "revoke"
        assert list(run.events.order_by("sequence").values_list("outcome", flat=True)) == [
            "accepted",
            "started",
            "accepted",
        ]
    finally:
        child.release.set()
        server.shutdown()
        server.server_close()
        controller.shutdown()
        controller.server_close()

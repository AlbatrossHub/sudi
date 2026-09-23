import json

from odoo import Command
from odoo.tests.common import HttpCase

FIELD_ROOT = "/api/field/v1"
CUSTOMER_ROOT = "/api/customer/v1"


class SudiApiCase(HttpCase):
    """Real HTTP against the mounted app.

    Deliberately not a plain ``TransactionCase`` with direct calls to the
    router functions: the whole point of stage 2 is the wiring -- the endpoint
    records, the bearer scheme, and above all the error envelope, which is
    produced by a middleware and therefore only observable in a real response.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.password = "s3cret-operator-pw"
        cls.operator = cls.env["res.users"].create({
            "name": "Ramesh Onfield",
            "login": "sudi_api_operator",
            "password": cls.password,
            "group_ids": [Command.set([
                cls.env.ref("diamond.group_sudi_pickup_delivery_operator").id,
            ])],
        })
        cls.job_worker = cls.env["res.users"].create({
            "name": "Jaya Job Work",
            "login": "sudi_api_jobworker",
            "password": cls.password,
            "group_ids": [Command.set([
                cls.env.ref("diamond.group_sudi_job_work_user").id,
            ])],
        })
        cls.operator_2 = cls.env["res.users"].create({
            "name": "Suresh Onfield",
            "login": "sudi_api_operator_2",
            "password": cls.password,
            "group_ids": [Command.set([
                cls.env.ref("diamond.group_sudi_pickup_delivery_operator").id,
            ])],
        })
        # An internal account with no app role at all: accounting, HR, the
        # integration user. It must not be able to get a token.
        cls.office = cls.env["res.users"].create({
            "name": "Office Only",
            "login": "sudi_api_office",
            "password": cls.password,
            "group_ids": [Command.set([cls.env.ref("base.group_user").id])],
        })

    def _post(self, path, payload, token=None, root=FIELD_ROOT, method="POST"):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        # The HTTP handler shares this test's cursor, but only sees what has
        # been flushed to it.
        self.env.flush_all()
        response = self.url_open(
            f"{root}{path}",
            data=json.dumps(payload) if payload is not None else None,
            headers=headers,
            method=method,
        )
        return response

    def _get(self, path, token=None, root=FIELD_ROOT):
        headers = {}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self.env.flush_all()
        return self.url_open(f"{root}{path}", headers=headers)

    def _login(self, user=None, device_uid="phone-aaaaaaaa", **device):
        payload = {
            "login": (user or self.operator).login,
            "password": self.password,
            "device": {"device_uid": device_uid, **device},
        }
        response = self._post("/auth/login", payload)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def assertEnvelope(self, response, status_code, code):
        """The error contract, asserted on the wire.

        Flat, not wrapped in FastAPI's ``detail``: an offline client branches on
        ``code`` and decides retry-versus-drop on ``retryable``, and it should
        not have to reach through a wrapper to find either.
        """
        self.assertEqual(response.status_code, status_code, response.text)
        body = response.json()
        self.assertEqual(body.get("code"), code, body)
        for key in ("message", "retryable", "resync", "detail"):
            self.assertIn(key, body, body)
        return body

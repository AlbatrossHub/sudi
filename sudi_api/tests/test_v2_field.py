import json
import uuid
from datetime import datetime, timedelta, timezone

from odoo import Command, fields
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from .test_intents import SudiIntentCase, _png, _png_b64


@tagged("post_install", "-at_install")
class SudiV2FieldCase(SudiIntentCase):
    """The v2 staff app: an office validator, and an operator who may post
    jangads on a customer's behalf."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.operator.sudo().sudi_can_post_jangad_on_behalf = True
        cls.validator = cls.env["res.users"].sudo().create({
            "name": "Meera Office",
            "login": "sudi_api_validator",
            "password": cls.password,
            "group_ids": [Command.set([
                cls.env.ref("diamond.group_sudi_office_validator").id,
            ])],
        })
        cls.partner.sudo().write({
            "phone": "+91 98250 11111",
            "street": "12, Mahidharpura",
            "city": "Surat",
        })
        cls.carat_type = cls.env["sudi.diamond.job.type"].sudo().create({
            "name": "API Carat Grading",
            "service_product_id": cls.job_type.service_product_id.id,
            "invoice_basis": "carats",
        })

    def _json(self, method, path, token, payload=None, key=None, headers=None):
        request_headers = {"Authorization": f"Bearer {token}"}
        if payload is not None:
            request_headers["Content-Type"] = "application/json"
        if key:
            request_headers["Idempotency-Key"] = key
        request_headers.update(headers or {})
        self.env.flush_all()
        return self.url_open(
            f"/api/field/v1{path}",
            data=json.dumps(payload) if payload is not None else None,
            headers=request_headers,
            method=method,
        )

    def _stage(self, token, raw=None):
        response = self._upload(token, raw=raw)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()["reference"]

    def _collected(self, services=None):
        """A jangad receipt collected by the field, waiting for the office."""
        receipt = self._pending_receipt()
        if services:
            receipt.sudo().write({"sudi_service_request_ids": [
                Command.create(service) for service in services
            ]})
        receipt.with_user(self.operator_2).action_sudi_confirm_pickup()
        self._settle()
        self.assertEqual(receipt.state, "draft")
        return receipt

    def _iso(self, delta):
        return (datetime.now(timezone.utc) + delta).isoformat()


class TestSudiServerTime(SudiV2FieldCase):

    def test_the_clock_needs_no_token(self):
        response = self.url_open("/api/field/v1/time")
        self.assertEqual(response.status_code, 200, response.text)
        stamp = datetime.fromisoformat(response.json()["server_time"].replace("Z", "+00:00"))
        self.assertIsNotNone(stamp.tzinfo)
        self.assertLess(abs((stamp - datetime.now(timezone.utc)).total_seconds()), 60)

    def test_the_customer_app_has_it_too(self):
        response = self.url_open("/api/customer/v1/time")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertIn("server_time", response.json())


class TestSudiCatalog(SudiV2FieldCase):

    def test_any_field_role_reads_the_services_with_their_units(self):
        token = self._token(user=self.job_worker)
        response = self._json("GET", "/catalog/job-types", token)
        self.assertEqual(response.status_code, 200, response.text)
        by_id = {entry["id"]: entry for entry in response.json()}
        self.assertEqual(by_id[self.job_type.id]["unit"], "pcs")
        self.assertEqual(by_id[self.carat_type.id]["unit"], "ct")
        self.assertIn("sequence", by_id[self.job_type.id])

    def test_an_unchanged_catalogue_is_a_304(self):
        token = self._token()
        first = self._json("GET", "/catalog/job-types", token)
        etag = first.headers["ETag"]

        again = self._json("GET", "/catalog/job-types", token, headers={"If-None-Match": etag})

        self.assertEqual(again.status_code, 304)

    def test_a_retired_service_is_not_offered(self):
        self.carat_type.sudo().active = False
        ids = [entry["id"] for entry in self._json("GET", "/catalog/job-types", self._token()).json()]
        self.assertNotIn(self.carat_type.id, ids)

    def test_the_catalogue_needs_a_token(self):
        self.assertEnvelope(self.url_open("/api/field/v1/catalog/job-types"), 401, "AUTH")


class TestSudiCustomerDirectory(SudiV2FieldCase):

    def test_the_directory_lists_customers_with_their_addresses(self):
        response = self._json("GET", "/customers", self._token())
        self.assertEqual(response.status_code, 200, response.text)
        entry = [row for row in response.json() if row["id"] == self.partner.id][0]
        self.assertEqual(entry["phone"], "9825011111")
        self.assertEqual(entry["gst_state"], "missing")
        self.assertEqual(entry["addresses"][0]["id"], self.partner.id)
        self.assertIn("Mahidharpura", entry["addresses"][0]["address"])
        self.assertTrue(entry["addresses"][0]["is_default"])

    def test_staff_the_company_the_archived_and_the_unreachable_are_left_out(self):
        self.operator_2.partner_id.sudo().phone = "9825022222"
        self.env.company.partner_id.sudo().phone = "9825033333"
        archived = self.env["res.partner"].sudo().create(
            {"name": "Gone Gems", "phone": "9825044444", "active": False}
        )
        no_phone = self.env["res.partner"].sudo().create({"name": "Silent Gems"})
        short = self.env["res.partner"].sudo().create({"name": "Short Gems", "phone": "12345"})
        # A WhatsApp group id the connector filed as a partner: its last ten
        # digits are not a phone number.
        group = self.env["res.partner"].sudo().create(
            {"name": "+120363176023391403", "phone": "+120363176023391403"}
        )

        ids = {row["id"] for row in self._json("GET", "/customers", self._token()).json()}

        self.assertIn(self.partner.id, ids)
        for record in (
            self.operator_2.partner_id, self.env.company.partner_id, archived, no_phone,
            short, group,
        ):
            self.assertNotIn(record.id, ids)

    def test_a_contact_persons_number_lists_their_company_once(self):
        company = self.env["res.partner"].sudo().create({"name": "Parent Gems", "is_company": True})
        self.env["res.partner"].sudo().create({
            "name": "Contact Person", "parent_id": company.id, "phone": "9825055555",
        })
        rows = [row for row in self._json("GET", "/customers", self._token()).json()
                if row["id"] == company.id]
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["phone"], "9825055555")

    def test_an_unchanged_directory_is_a_304(self):
        token = self._token()
        etag = self._json("GET", "/customers", token).headers["ETag"]
        again = self._json("GET", "/customers", token, headers={"If-None-Match": etag})
        self.assertEqual(again.status_code, 304)

    def test_it_needs_the_tick(self):
        response = self._json("GET", "/customers", self._token(user=self.operator_2))
        self.assertEnvelope(response, 403, "AUTH")

    def test_the_tick_alone_does_not_open_the_app(self):
        self.office.sudo().sudi_can_post_jangad_on_behalf = True
        self.env.flush_all()
        response = self._post("/auth/login", {
            "login": self.office.login, "password": self.password,
            "device": {"device_uid": "tick-only-0001"},
        })
        self.assertEnvelope(response, 403, "AUTH")


class TestSudiPostOnBehalf(SudiV2FieldCase):

    def _payload(self, token, **overrides):
        payload = {
            "customer": {"partner_id": self.partner.id},
            "pickup_address_id": self.partner.id,
            "services": [
                {"job_type_id": self.job_type.id, "quantity": 120, "remarks": "big stones first"},
                {"job_type_id": self.carat_type.id, "quantity": 3.5},
            ],
            "upload_ids": [self._stage(token), self._stage(token, _png((8, 8)))],
            "note": "customer will hand over at the gate",
        }
        payload.update(overrides)
        return payload

    def _receipt(self, body):
        return self.env["stock.picking"].sudo().browse(body["picking_id"])

    def test_posting_builds_the_same_request_a_customer_would(self):
        token = self._token()
        response = self._json("POST", "/jangads", token, self._payload(token))

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        receipt = self._receipt(body)
        self.assertEqual(body["reference"], receipt.name)
        self.assertEqual(receipt.state, "sudi_pickup_pending")
        self.assertEqual(receipt.partner_id, self.partner)
        self.assertEqual(receipt.sudi_customer_contact, "9825011111")
        self.assertEqual(receipt.sudi_jangad_source, "on_behalf")
        self.assertEqual(receipt.sudi_posted_by_id, self.operator)
        self.assertEqual(receipt.sudi_jangad_page_count, 2)
        self.assertEqual(
            receipt.sudi_service_request_ids.mapped("quantity"), [120.0, 3.5]
        )
        # The pickup document, ready for the app to show and to collect.
        self.assertEqual(body["pickup"]["id"], receipt.id)
        self.assertEqual(body["pickup"]["stage"], "awaiting")
        self.assertEqual(len(body["pickup"]["services"]), 2)
        self.assertEqual(body["pickup"]["services"][1]["unit"], "ct")

    def test_the_chatter_names_the_operator_and_the_capture_time(self):
        token = self._token()
        captured = (fields.Datetime.now() - timedelta(hours=3)).replace(microsecond=0)
        payload = self._payload(token, occurred_at=captured.isoformat() + "Z")

        receipt = self._receipt(self._json("POST", "/jangads", token, payload).json())

        bodies = " ".join(receipt.message_ids.mapped("body"))
        self.assertIn("Jangad posted for the customer recorded by Ramesh Onfield", bodies)
        self.assertIn(str(captured), bodies)
        self.assertIn("customer will hand over at the gate", bodies)

    def test_a_new_number_creates_the_customer_under_the_name_given(self):
        token = self._token()
        payload = self._payload(
            token,
            customer={"phone": "98250 66666", "name": "Walk-in Gems"},
            pickup_address_id=None,
            manual_pickup_address="7, Varachha Road, Surat",
        )

        response = self._json("POST", "/jangads", token, payload)

        self.assertEqual(response.status_code, 200, response.text)
        receipt = self._receipt(response.json())
        self.assertEqual(receipt.partner_id.name, "Walk-in Gems")
        self.assertEqual(receipt.sudi_customer_contact, "9825066666")
        self.assertIn("Varachha", receipt.sudi_pickup_address)

    def test_a_staff_number_is_refused(self):
        self.operator_2.partner_id.sudo().phone = "9825077777"
        token = self._token()
        payload = self._payload(
            token, customer={"phone": "9825077777"},
            pickup_address_id=None, manual_pickup_address="Somewhere",
        )

        response = self._json("POST", "/jangads", token, payload)

        body = self.assertEnvelope(response, 422, "VALIDATION")
        self.assertEqual(body["detail"]["reason"], "staff_phone")

    def test_a_retry_with_the_same_key_posts_once(self):
        token = self._token()
        payload = self._payload(token)
        first = self._json("POST", "/jangads", token, payload, key="behalf-1")
        second = self._json("POST", "/jangads", token, payload, key="behalf-1")

        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual(first.json(), second.json())
        self.assertEqual(
            self.env["stock.picking"].sudo().search_count(
                [("sudi_posted_by_id", "=", self.operator.id)]
            ),
            1,
        )

    def test_a_capture_older_than_the_window_is_stale(self):
        token = self._token()
        payload = self._payload(token, occurred_at=self._iso(timedelta(hours=-100)))
        self.assertEnvelope(self._json("POST", "/jangads", token, payload), 409, "STALE_INTENT")

    def test_a_clock_far_ahead_is_refused(self):
        token = self._token()
        payload = self._payload(token, occurred_at=self._iso(timedelta(hours=3)))
        self.assertEnvelope(self._json("POST", "/jangads", token, payload), 422, "CLOCK_SKEW")

    def test_a_retired_service_refuses_it_and_leaves_the_pages_free(self):
        token = self._token()
        payload = self._payload(token)
        self.carat_type.sudo().active = False

        response = self._json("POST", "/jangads", token, payload)

        self.assertEnvelope(response, 422, "VALIDATION")
        self.env.invalidate_all()
        uploads = self.env["sudi.upload"].sudo().search(
            [("reference", "in", payload["upload_ids"])]
        )
        self.assertEqual(len(uploads), 2)
        self.assertFalse(any(uploads.mapped("consumed_at")))

    def test_it_needs_the_tick(self):
        token = self._token(user=self.operator_2)
        payload = {
            "customer": {"partner_id": self.partner.id},
            "pickup_address_id": self.partner.id,
            "upload_ids": ["whatever"],
        }
        self.assertEnvelope(self._json("POST", "/jangads", token, payload), 403, "AUTH")

    def test_both_or_neither_customer_is_a_validation_error(self):
        token = self._token()
        payload = self._payload(token, customer={"partner_id": self.partner.id, "phone": "9825011111"})
        self.assertEnvelope(self._json("POST", "/jangads", token, payload), 422, "VALIDATION")

    def test_the_other_pickup_people_hear_about_it(self):
        token = self._token()
        receipt = self._receipt(self._json("POST", "/jangads", token, self._payload(token)).json())
        notices = self.env["sudi.app.notice"].sudo().search(
            [("picking_id", "=", receipt.id), ("kind", "=", "pickup_new")]
        )
        self.assertIn(self.operator_2, notices.user_id)
        self.assertNotIn(self.operator, notices.user_id)

    def test_the_posted_jangad_is_collected_the_normal_way(self):
        token = self._token()
        body = self._json("POST", "/jangads", token, self._payload(token)).json()
        response = self._intent(f"/pickups/{body['picking_id']}/confirm", {}, token)
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["record"]["stage"], "collected")


class TestSudiOfficeScopeApi(SudiV2FieldCase):

    def test_the_office_pulls_collected_jangads(self):
        receipt = self._collected([{"job_type_id": self.job_type.id, "quantity": 4}])
        token = self._token(user=self.validator)

        body = self._json("GET", "/sync/pull?scopes=office", token).json()

        docs = [doc for doc in body["scopes"]["office"]["upserts"] if doc["id"] == receipt.id]
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["services"][0]["quantity"], 4.0)
        self.assertEqual(docs[0]["items"], [])
        self.assertIsNone(body["scopes"]["pickup"])

    def test_the_office_can_read_the_jangad_pages(self):
        receipt = self._collected()
        receipt.sudo().sudi_jangad_image = _png_b64()
        self._settle()
        token = self._token(user=self.validator)

        response = self._json("GET", f"/pickups/{receipt.id}/jangad/0", token)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.content)

    def test_a_pickup_person_cannot_pull_the_office_scope(self):
        response = self._json("GET", "/sync/pull?scopes=office", self._token(user=self.operator_2))
        self.assertEnvelope(response, 403, "AUTH")

    def test_the_office_role_alone_signs_in(self):
        self.assertIn("office", self._login(user=self.validator, device_uid="office-0001")["roles"])


class TestSudiOfficeLines(SudiV2FieldCase):

    def _lines(self, receipt, lines, token=None, key=None):
        return self._json(
            "PUT", f"/receipts/{receipt.id}/lines", token or self._token(user=self.validator),
            {"lines": lines}, key=key,
        )

    def test_the_table_is_replaced_in_the_order_sent(self):
        receipt = self._collected([{"job_type_id": self.job_type.id, "quantity": 4}])
        request = receipt.sudi_service_request_ids

        response = self._lines(receipt, [
            {"size": "0.30", "pcs": 24, "carats": 7.85, "job_type_id": self.job_type.id,
             "remarks": "chip on 4", "request_id": request.id},
            {"size": "0.50", "pcs": 6, "carats": 3.1, "job_type_id": self.carat_type.id},
        ])

        self.assertEqual(response.status_code, 200, response.text)
        doc = response.json()
        self.assertEqual([item["sr"] for item in doc["items"]], [1, 2])
        self.assertEqual(doc["items"][0]["remarks"], "chip on 4")
        moves = receipt.move_ids.sorted("sudi_sr")
        self.assertEqual(moves.mapped("sudi_pcs_qty"), [24.0, 6.0])
        # Pieces are the demand; the API write must not leave it behind.
        self.assertEqual(moves.mapped("product_uom_qty"), [24.0, 6.0])
        self.assertEqual(request.move_id, moves[0])
        self.assertEqual(doc["services"][0]["move_id"], moves[0].id)

    def test_a_second_save_updates_kept_rows_and_drops_the_rest(self):
        receipt = self._collected()
        first = self._lines(receipt, [
            {"pcs": 5, "job_type_id": self.job_type.id},
            {"pcs": 6, "job_type_id": self.job_type.id},
        ]).json()
        keep = first["items"][1]["move_id"]

        second = self._lines(receipt, [
            {"move_id": keep, "pcs": 9, "job_type_id": self.job_type.id},
            {"pcs": 1, "job_type_id": self.carat_type.id},
        ])

        self.assertEqual(second.status_code, 200, second.text)
        items = second.json()["items"]
        self.assertEqual(items[0]["move_id"], keep)
        self.assertEqual(items[0]["sr"], 1)
        self.assertEqual(items[0]["pcs"], 9.0)
        self.assertEqual(len(receipt.move_ids), 2)
        self.assertNotIn(first["items"][0]["move_id"], receipt.move_ids.ids)

    def test_a_row_from_another_receipt_is_refused_with_its_reason(self):
        receipt = self._collected()
        other = self._assigned_receipt()

        response = self._lines(receipt, [{"move_id": other.move_ids.id, "pcs": 1}])

        body = self.assertEnvelope(response, 422, "VALIDATION")
        self.assertEqual(body["detail"]["problems"][0]["row"], 1)
        self.assertEqual(body["detail"]["problems"][0]["field"], "move_id")
        self.assertEqual(other.move_ids.sudi_pcs_qty, 10.0)

    def test_a_receipt_not_collected_yet_is_refused(self):
        receipt = self._pending_receipt()
        body = self.assertEnvelope(self._lines(receipt, []), 422, "VALIDATION")
        self.assertEqual(body["detail"]["reason"], "not_collected")

    def test_a_receipt_already_in_job_work_is_a_conflict(self):
        receipt = self._assigned_receipt()
        body = self.assertEnvelope(self._lines(receipt, []), 409, "ALREADY_CONFIRMED")
        self.assertEqual(body["resync"], [receipt.id])

    def test_only_the_office_edits_the_table(self):
        receipt = self._collected()
        response = self._lines(receipt, [], token=self._token())
        self.assertEnvelope(response, 403, "AUTH")

    def test_entries_from_the_requested_services(self):
        receipt = self._collected([
            {"job_type_id": self.job_type.id, "quantity": 120},
            {"job_type_id": self.carat_type.id, "quantity": 2.5},
        ])
        token = self._token(user=self.validator)

        first = self._json("POST", f"/receipts/{receipt.id}/lines/from-requests", token)
        again = self._json("POST", f"/receipts/{receipt.id}/lines/from-requests", token)

        self.assertEqual(first.status_code, 200, first.text)
        items = first.json()["items"]
        self.assertEqual([item["pcs"] for item in items], [120.0, 0.0])
        self.assertEqual([item["carats"] for item in items], [0.0, 2.5])
        self.assertEqual(len(again.json()["items"]), 2)
        self.assertTrue(all(service["move_id"] for service in again.json()["services"]))


class TestSudiOfficeReady(SudiV2FieldCase):

    def _ready(self, receipt, token=None, key=None):
        return self._json(
            "POST", f"/receipts/{receipt.id}/ready", token or self._token(user=self.validator),
            key=key,
        )

    def _complete(self):
        receipt = self._collected([{"job_type_id": self.job_type.id, "quantity": 12}])
        receipt._sudi_add_lines_from_requests()
        self._settle()
        return receipt

    def test_every_problem_comes_back_at_once(self):
        receipt = self._collected()
        self._lines_raw(receipt, [{"pcs": 0}, {"pcs": 3, "job_type_id": self.job_type.id}])

        body = self.assertEnvelope(self._ready(receipt), 422, "VALIDATION")

        fields_ = sorted(problem["field"] for problem in body["detail"]["problems"])
        self.assertEqual(fields_, ["job_type_id", "pcs"])
        self.assertIn("(and 1 more)", body["message"])
        self.assertEqual(receipt.state, "draft")

    def _lines_raw(self, receipt, lines):
        response = self._json(
            "PUT", f"/receipts/{receipt.id}/lines", self._token(user=self.validator),
            {"lines": lines},
        )
        self.assertEqual(response.status_code, 200, response.text)

    def test_an_empty_table_is_not_ready(self):
        receipt = self._collected()
        body = self.assertEnvelope(self._ready(receipt), 422, "VALIDATION")
        self.assertEqual(body["detail"]["problems"][0]["field"], "lines")

    def test_ready_starts_job_work_and_rings_the_job_workers(self):
        receipt = self._complete()

        response = self._ready(receipt)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), {
            "code": "OK", "state": "assigned", "reference": receipt.name,
        })
        self.assertEqual(receipt.state, "assigned")
        notices = self.env["sudi.app.notice"].sudo().search(
            [("picking_id", "=", receipt.id), ("kind", "=", "jobwork_ready")]
        )
        self.assertIn(self.job_worker, notices.user_id)
        self.assertNotIn(self.validator, notices.user_id)
        bodies = " ".join(receipt.message_ids.mapped("body"))
        self.assertIn("Marked ready for job work recorded by Meera Office", bodies)

    def test_editing_the_table_then_ready_still_rings_the_job_workers(self):
        # Found on the phone: the PUT before READY edited an existing line, the
        # quantity sync confirmed the receipt there and then, READY answered
        # ALREADY_DONE and no job worker heard about it.
        receipt = self._complete()
        move = receipt.move_ids
        self._lines_raw(receipt, [
            {"move_id": move.id, "size": "+11", "pcs": 12, "carats": 9.25,
             "job_type_id": self.job_type.id, "remarks": "big stones first"},
        ])
        self.assertEqual(receipt.state, "draft", "editing the table must not mark it ready")

        response = self._ready(receipt)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["code"], "OK")
        notices = self.env["sudi.app.notice"].sudo().search(
            [("picking_id", "=", receipt.id), ("kind", "=", "jobwork_ready")]
        )
        self.assertIn(self.job_worker, notices.user_id)

    def test_ready_twice_is_already_done(self):
        receipt = self._complete()
        token = self._token(user=self.validator)
        self._ready(receipt, token=token)
        again = self._ready(receipt, token=token)
        self.assertEqual(again.status_code, 200, again.text)
        self.assertEqual(again.json()["code"], "ALREADY_DONE")

    def test_a_replay_with_the_same_key_is_the_first_answer(self):
        receipt = self._complete()
        token = self._token(user=self.validator)
        first = self._ready(receipt, token=token, key="ready-1")
        second = self._ready(receipt, token=token, key="ready-1")
        self.assertEqual(first.json(), second.json())
        self.assertEqual(second.json()["code"], "OK")

    def test_ready_leaves_the_office_list(self):
        receipt = self._complete()
        token = self._token(user=self.validator)
        self._ready(receipt, token=token)
        body = self._json("GET", "/sync/pull?scopes=office", token).json()
        self.assertNotIn(receipt.id, [doc["id"] for doc in body["scopes"]["office"]["upserts"]])

    def test_only_the_office_marks_ready(self):
        receipt = self._complete()
        self.assertEnvelope(self._ready(receipt, token=self._token()), 403, "AUTH")


class TestSudiManualTime(SudiV2FieldCase):

    def _log(self, receipt, payload, token=None, key=None, user=None):
        return self._json(
            "POST", f"/jobwork/{receipt.id}/timesheets",
            token or self._token(user=user or self.job_worker), payload, key=key,
        )

    def test_logging_time_books_it_and_returns_the_document(self):
        receipt = self._assigned_receipt()

        response = self._log(receipt, {
            "job_type_id": self.job_type.id, "hours": 1.5, "description": "Sorting",
        })

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["code"], "OK")
        self.assertEqual(body["entry"]["hours"], 1.5)
        self.assertEqual(body["entry"]["description"], "Sorting")
        self.assertEqual(body["entry"]["picking"]["id"], receipt.id)
        self.assertFalse(body["entry"]["running"])
        self.assertEqual(body["jobwork"]["my_hours"], 1.5)
        self.assertEqual(body["jobwork"]["total_hours"], 1.5)

    def test_a_retried_flush_books_the_time_once(self):
        receipt = self._assigned_receipt()
        token = self._token(user=self.job_worker)
        payload = {"job_type_id": self.job_type.id, "hours": 2}
        first = self._log(receipt, payload, token=token, key="time-1")
        second = self._log(receipt, payload, token=token, key="time-1")
        self.assertEqual(first.json(), second.json())
        self.assertEqual(receipt.sudi_total_hours_spent, 2.0)

    def test_an_offline_entry_is_dated_the_day_it_was_captured(self):
        receipt = self._assigned_receipt()
        captured = datetime.now(timezone.utc) - timedelta(hours=30)
        local_day = fields.Datetime.context_timestamp(
            self.job_worker, captured.replace(tzinfo=None)
        ).date()

        body = self._log(receipt, {
            "job_type_id": self.job_type.id, "hours": 1, "occurred_at": captured.isoformat(),
        }).json()

        self.assertEqual(body["entry"]["date"], local_day.isoformat())

    def test_a_day_after_the_capture_is_refused(self):
        receipt = self._assigned_receipt()
        tomorrow = fields.Date.context_today(self.job_worker) + timedelta(days=2)
        response = self._log(receipt, {
            "job_type_id": self.job_type.id, "hours": 1, "date": tomorrow.isoformat(),
        })
        self.assertEnvelope(response, 422, "VALIDATION")

    def test_a_stale_capture_is_refused(self):
        receipt = self._assigned_receipt()
        response = self._log(receipt, {
            "job_type_id": self.job_type.id, "hours": 1,
            "occurred_at": self._iso(timedelta(hours=-100)),
        })
        self.assertEnvelope(response, 409, "STALE_INTENT")

    def test_more_than_a_day_is_a_validation_error(self):
        receipt = self._assigned_receipt()
        response = self._log(receipt, {"job_type_id": self.job_type.id, "hours": 25})
        self.assertEnvelope(response, 422, "VALIDATION")

    def test_a_receipt_outside_job_work_is_not_found(self):
        receipt = self._pending_receipt()
        response = self._log(receipt, {"job_type_id": self.job_type.id, "hours": 1})
        self.assertEnvelope(response, 404, "NOT_IN_SCOPE")

    def test_a_pickup_only_account_cannot_log_time(self):
        receipt = self._assigned_receipt()
        response = self._log(receipt, {"job_type_id": self.job_type.id, "hours": 1}, user=self.operator_2)
        self.assertEnvelope(response, 403, "AUTH")

    def test_no_employee_is_a_validation_error(self):
        receipt = self._assigned_receipt()
        self.env["hr.employee"].sudo().search([("user_id", "=", self.job_worker.id)]).user_id = False
        response = self._log(receipt, {"job_type_id": self.job_type.id, "hours": 1})
        self.assertEnvelope(response, 422, "VALIDATION")


class TestSudiProductivity(SudiV2FieldCase):

    def test_my_work_sums_by_day_service_and_entry(self):
        receipt = self._assigned_receipt()
        as_worker = receipt.with_user(self.job_worker)
        today = fields.Date.context_today(self.job_worker)
        as_worker._sudi_log_time(self.job_type, 2.0, date=today)
        as_worker._sudi_log_time(self.carat_type, 0.5, date=today)
        receipt.with_user(self.operator)._sudi_log_time(self.job_type, 9.0, date=today)

        response = self._json("GET", "/me/productivity", self._token(user=self.job_worker))

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["total_hours"], 2.5)
        self.assertEqual(len(body["by_day"]), 7)
        self.assertEqual(
            {row["date"]: row["hours"] for row in body["by_day"]}[today.isoformat()], 2.5
        )
        self.assertEqual(body["by_job_type"][0]["job_type"]["id"], self.job_type.id)
        self.assertEqual(body["by_job_type"][0]["hours"], 2.0)
        self.assertEqual(len(body["entries"]), 2)
        self.assertEqual(body["entries"][0]["picking"]["name"], receipt.name)

    def test_a_range_can_be_asked_for(self):
        response = self._json(
            "GET", "/me/productivity?date_from=2026-10-01&date_to=2026-10-03",
            self._token(user=self.job_worker),
        )
        body = response.json()
        self.assertEqual([row["date"] for row in body["by_day"]],
                         ["2026-10-01", "2026-10-02", "2026-10-03"])

    def test_a_backwards_or_huge_range_is_refused(self):
        token = self._token(user=self.job_worker)
        self.assertEnvelope(self._json(
            "GET", "/me/productivity?date_from=2026-10-05&date_to=2026-10-01", token,
        ), 422, "VALIDATION")
        self.assertEnvelope(self._json(
            "GET", "/me/productivity?date_from=2026-01-01&date_to=2026-12-31", token,
        ), 422, "VALIDATION")

    def test_it_is_for_job_workers(self):
        response = self._json("GET", "/me/productivity", self._token(user=self.operator_2))
        self.assertEnvelope(response, 403, "AUTH")


class TestSudiNoticesApi(SudiV2FieldCase):

    def setUp(self):
        super().setUp()
        # Creating the receipt rings the pickup bell by itself; start each
        # test from an empty bell so the counts below are the test's own.
        self.receipt = self._pending_receipt()
        self.env["sudi.app.notice"].sudo().search([]).unlink()

    def _notify(self, user, count=1):
        return self.env["sudi.app.notice"].sudo().create([
            {
                "user_id": user.id, "kind": "pickup_new", "title": f"New pickup {index}",
                "picking_id": self.receipt.id, "scope": "pickup",
            }
            for index in range(count)
        ])

    def test_the_bell_lists_my_notices_newest_first(self):
        mine = self._notify(self.operator_2, 3)
        self._notify(self.validator)

        body = self._json("GET", "/notices", self._token(user=self.operator_2)).json()

        self.assertEqual([notice["id"] for notice in body["notices"]], sorted(mine.ids, reverse=True))
        self.assertEqual(body["unread"], 3)
        self.assertEqual(body["last_id"], max(mine.ids))
        self.assertFalse(body["notices"][0]["read"])
        self.assertEqual(body["notices"][0]["scope"], "pickup")

    def test_after_pages_forward_without_a_gap(self):
        mine = self._notify(self.operator_2, 5)
        token = self._token(user=self.operator_2)
        after = min(mine.ids) - 1

        page = self._json("GET", f"/notices?after={after}&limit=2", token).json()

        self.assertEqual(sorted(notice["id"] for notice in page["notices"]), sorted(mine.ids)[:2])
        self.assertTrue(page["has_more"])
        rest = self._json("GET", f"/notices?after={page['last_id']}&limit=10", token).json()
        self.assertEqual(sorted(notice["id"] for notice in rest["notices"]), sorted(mine.ids)[2:])
        self.assertFalse(rest["has_more"])

    def test_marking_read_by_ids_and_up_to(self):
        mine = self._notify(self.operator_2, 3)
        theirs = self._notify(self.validator)
        token = self._token(user=self.operator_2)
        ids = sorted(mine.ids)

        first = self._json("POST", "/notices/read", token, {"ids": [ids[0], theirs.id]}).json()
        self.assertEqual(first, {"marked": 1, "unread": 2})
        rest = self._json("POST", "/notices/read", token, {"up_to": ids[-1]}).json()
        self.assertEqual(rest, {"marked": 2, "unread": 0})
        again = self._json("POST", "/notices/read", token, {"up_to": ids[-1]}).json()
        self.assertEqual(again, {"marked": 0, "unread": 0})
        self.assertFalse(theirs.read_at)

    def test_ids_or_up_to_not_both(self):
        response = self._json("POST", "/notices/read", self._token(), {"ids": [1], "up_to": 3})
        self.assertEnvelope(response, 422, "VALIDATION")


class TestSudiRefusalRollsBack(SudiV2FieldCase):
    """Regression: the envelope used to be rendered with the work still in place.

    The middleware turns the exception into a response, so nothing reached the
    HTTP layer to tell it to roll back -- and it committed the first half of
    every refused intent.
    """

    def _break_confirm(self):
        Picking = type(self.env["stock.picking"])
        original = Picking.action_sudi_confirm_pickup

        def refuse(records, occurred_at=None, location=None):
            raise UserError("The shop is closed.")

        Picking.action_sudi_confirm_pickup = refuse
        self.addCleanup(setattr, Picking, "action_sudi_confirm_pickup", original)
        return lambda: setattr(Picking, "action_sudi_confirm_pickup", original)

    def test_a_refused_intent_leaves_no_half_done_work(self):
        receipt = self._pending_receipt()
        token = self._token()
        page = self._stage(token)
        restore = self._break_confirm()

        response = self._intent(
            f"/pickups/{receipt.id}/confirm",
            {"upload_ids": [page], "note": "half done"},
            token, key="rollback-1",
        )

        self.assertEnvelope(response, 422, "VALIDATION")
        self.env.invalidate_all()
        upload = self.env["sudi.upload"].sudo().search([("reference", "=", page)])
        self.assertFalse(upload.consumed_at)
        self.assertFalse(receipt.sudi_jangad_image)
        self.assertNotIn("half done", " ".join(receipt.message_ids.mapped("body")))
        self.assertFalse(self.env["sudi.idempotency.key"].sudo().search(
            [("key", "=", "rollback-1")]
        ))

        # And the key is free again: the retry acts, it is not LOCKED for ever.
        restore()
        retry = self._intent(
            f"/pickups/{receipt.id}/confirm", {"upload_ids": [page]}, token, key="rollback-1",
        )
        self.assertEqual(retry.status_code, 200, retry.text)
        self.assertEqual(retry.json()["code"], "OK")

    def test_an_otp_refusal_still_counts_the_attempt(self):
        # The one deliberate exception: see customer_auth_router._otp_failure.
        Otp = type(self.env["sudi.auth.otp"])
        original = Otp._sudi_deliver
        Otp._sudi_deliver = lambda records, code: True
        self.addCleanup(setattr, Otp, "_sudi_deliver", original)
        self.env.flush_all()
        self.url_open(
            "/api/customer/v1/auth/otp/request",
            data=json.dumps({"phone": "9811100000"}),
            headers={"Content-Type": "application/json"},
        )

        self.url_open(
            "/api/customer/v1/auth/otp/verify",
            data=json.dumps({
                "phone": "9811100000", "code": "000000",
                "device": {"device_uid": f"otp-{uuid.uuid4().hex[:8]}"},
            }),
            headers={"Content-Type": "application/json"},
        )

        self.env.invalidate_all()
        otp = self.env["sudi.auth.otp"].sudo().search([("identifier", "=", "9811100000")])
        self.assertEqual(otp.attempts, 1)

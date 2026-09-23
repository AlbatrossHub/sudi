from odoo import Command, fields
from odoo.tests.common import tagged

from .common import CUSTOMER_ROOT, SudiApiCase


@tagged("post_install", "-at_install")
class SudiSyncApiCase(SudiApiCase):
    """A little diamond data, and the flush that makes the log real."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Both roles, so one token can ask for every scope.
        cls.operator.sudo().write({"group_ids": [
            Command.link(cls.env.ref("diamond.group_sudi_job_work_user").id)
        ]})
        cls.env["ir.config_parameter"].sudo().set_param(
            "sudi_sync.visibility_lag_seconds", "0"
        )
        cls.partner = cls.env["res.partner"].create({"name": "Kiran Gems"})
        warehouse = cls.env["stock.warehouse"].sudo().search([], limit=1)
        cls.picking_type_in = warehouse.in_type_id

    def _sync_flush(self):
        """What Cursor.commit does, without committing."""
        self.env.flush_all()
        self.env.cr.precommit.run()

    def _pending_receipt(self, phone="9876543210"):
        receipt = self.env["stock.picking"].create({
            "partner_id": self.partner.id,
            "picking_type_id": self.picking_type_in.id,
            "location_id": self.picking_type_in.default_location_src_id.id,
            "location_dest_id": self.picking_type_in.default_location_dest_id.id,
            "sudi_is_diamond_job_work": True,
            "sudi_customer_contact": phone,
            "sudi_pickup_address": "12, Mahidharpura, Surat",
        })
        self._sync_flush()
        return receipt

    def _pull(self, token, params="", headers=None):
        self.env.flush_all()
        request_headers = {"Authorization": f"Bearer {token}"}
        request_headers.update(headers or {})
        return self.url_open(f"/api/field/v1/sync/pull{params}", headers=request_headers)


class TestSudiSyncPullAccess(SudiSyncApiCase):

    def test_a_pull_needs_a_token(self):
        self.assertEnvelope(self.url_open("/api/field/v1/sync/pull"), 401, "AUTH")

    def test_the_customer_api_does_not_expose_the_field_pull(self):
        response = self.url_open(f"{CUSTOMER_ROOT}/sync/pull")
        self.assertEqual(response.status_code, 404, response.text)

    def test_scopes_default_to_what_the_roles_allow(self):
        token = self._login(user=self.job_worker, device_uid="sync-jw000001")["access_token"]
        body = self._pull(token).json()
        self.assertEqual(
            {scope for scope, value in body["scopes"].items() if value is not None},
            {"jobwork"},
        )

    def test_asking_for_a_scope_you_have_no_role_for_is_refused(self):
        # The client knows its roles from the token, so this is a client bug
        # worth surfacing rather than an empty response worth puzzling over.
        token = self._login(user=self.job_worker, device_uid="sync-jw000002")["access_token"]
        response = self._pull(token, "?scopes=delivery")
        body = self.assertEnvelope(response, 403, "AUTH")
        self.assertEqual(body["detail"]["entitled"], ["jobwork"])

    def test_an_unknown_scope_name_is_a_validation_error(self):
        token = self._login(device_uid="sync-op000003")["access_token"]
        response = self._pull(token, "?scopes=pickup,invented")
        body = self.assertEnvelope(response, 422, "VALIDATION")
        self.assertIn("pickup", body["detail"]["known"])


class TestSudiSyncPullContent(SudiSyncApiCase):

    def test_a_first_pull_is_a_full_resync_carrying_the_work(self):
        receipt = self._pending_receipt()
        token = self._login(device_uid="sync-op000010")["access_token"]

        body = self._pull(token, "?scopes=pickup").json()

        self.assertTrue(body["full_resync"])
        self.assertFalse(body["has_more"])
        self.assertTrue(body["server_time"])
        docs = body["scopes"]["pickup"]["upserts"]
        mine = [doc for doc in docs if doc["id"] == receipt.id]
        self.assertEqual(len(mine), 1)
        self.assertEqual(mine[0]["stage"], "awaiting")
        self.assertEqual(mine[0]["contact_phone"], "9876543210")
        self.assertEqual(mine[0]["customer"]["id"], self.partner.id)
        self.assertIn("T", mine[0]["created_at"])

    def test_the_second_pull_is_incremental_and_quiet(self):
        self._pending_receipt()
        token = self._login(device_uid="sync-op000011")["access_token"]
        cursor = self._pull(token, "?scopes=pickup").json()["cursor"]

        body = self._pull(token, f"?scopes=pickup&cursor={cursor}").json()

        self.assertFalse(body["full_resync"])
        self.assertEqual(body["scopes"]["pickup"]["upserts"], [])
        self.assertEqual(body["scopes"]["pickup"]["gone"], [])

    def test_only_the_changed_receipt_comes_back(self):
        first = self._pending_receipt()
        self._pending_receipt()
        token = self._login(device_uid="sync-op000012")["access_token"]
        cursor = self._pull(token, "?scopes=pickup").json()["cursor"]

        first.sudi_internal_notes = "call before arriving"
        self._sync_flush()
        body = self._pull(token, f"?scopes=pickup&cursor={cursor}").json()

        self.assertEqual(
            [doc["id"] for doc in body["scopes"]["pickup"]["upserts"]], [first.id]
        )
        self.assertGreater(body["cursor"], cursor)

    def test_a_receipt_collected_by_someone_else_is_reported_as_gone(self):
        receipt = self._pending_receipt()
        token = self._login(device_uid="sync-op000013")["access_token"]
        cursor = self._pull(token, "?scopes=pickup").json()["cursor"]

        receipt.with_user(self.operator_2).action_sudi_confirm_pickup()
        self._sync_flush()
        body = self._pull(token, f"?scopes=pickup&cursor={cursor}").json()

        self.assertIn(receipt.id, body["scopes"]["pickup"]["gone"])
        self.assertNotIn(
            receipt.id, [doc["id"] for doc in body["scopes"]["pickup"]["upserts"]]
        )

    def test_a_receipt_i_collected_stays_on_my_list_as_collected(self):
        receipt = self._pending_receipt()
        token = self._login(device_uid="sync-op000016")["access_token"]
        cursor = self._pull(token, "?scopes=pickup").json()["cursor"]

        receipt.with_user(self.operator).action_sudi_confirm_pickup()
        self._sync_flush()
        body = self._pull(token, f"?scopes=pickup&cursor={cursor}").json()

        docs = [
            doc for doc in body["scopes"]["pickup"]["upserts"]
            if doc["id"] == receipt.id
        ]
        self.assertEqual(len(docs), 1, body["scopes"]["pickup"])
        self.assertEqual(docs[0]["stage"], "collected")
        self.assertEqual(docs[0]["collected_by"]["id"], self.operator.id)
        self.assertNotIn(receipt.id, body["scopes"]["pickup"]["gone"])

    def test_a_stale_cursor_is_answered_with_a_full_resync(self):
        self._pending_receipt()
        token = self._login(device_uid="sync-op000014")["access_token"]
        self.env["ir.config_parameter"].sudo().set_param(
            "sudi_sync.pruned_through", "999999"
        )
        body = self._pull(token, "?scopes=pickup&cursor=5").json()
        self.assertTrue(body["full_resync"])

    def test_the_device_records_where_it_got_to(self):
        self._pending_receipt()
        token = self._login(device_uid="sync-op000015")["access_token"]
        cursor = self._pull(token, "?scopes=pickup").json()["cursor"]
        device = self.env["sudi.api.device"].sudo().search([
            ("device_uid", "=", "sync-op000015")
        ])
        self.assertEqual(device.sync_cursor, cursor)


class TestSudiSyncPullPaging(SudiSyncApiCase):

    def test_a_full_resync_pages_and_resumes(self):
        first = self._pending_receipt()
        second = self._pending_receipt()
        token = self._login(device_uid="sync-op000020")["access_token"]

        page = self._pull(token, "?scopes=pickup&limit=1").json()
        self.assertTrue(page["has_more"])
        self.assertEqual(page["next_after_scope"], "pickup")
        first_ids = [doc["id"] for doc in page["scopes"]["pickup"]["upserts"]]

        rest = self._pull(
            token,
            f"?scopes=pickup&limit=1&cursor={page['cursor']}"
            f"&after_scope={page['next_after_scope']}&after_id={page['next_after_id']}",
        ).json()

        # The cursor from page one survives paging, so a change committed
        # meanwhile is not skipped.
        self.assertEqual(rest["cursor"], page["cursor"])
        rest_ids = [doc["id"] for doc in rest["scopes"]["pickup"]["upserts"]]
        self.assertEqual(set(first_ids) | set(rest_ids), {first.id, second.id})

    def test_resuming_a_scope_that_is_not_being_pulled_is_refused(self):
        token = self._login(device_uid="sync-op000021")["access_token"]
        response = self._pull(token, "?scopes=pickup&after_scope=jobwork&after_id=1")
        self.assertEnvelope(response, 422, "VALIDATION")

    def test_a_delta_pages_by_advancing_the_cursor(self):
        first = self._pending_receipt()
        second = self._pending_receipt()
        token = self._login(device_uid="sync-op000022")["access_token"]
        cursor = self._pull(token, "?scopes=pickup").json()["cursor"]

        first.sudi_internal_notes = "one"
        self._sync_flush()
        second.sudi_internal_notes = "two"
        self._sync_flush()

        page = self._pull(token, f"?scopes=pickup&cursor={cursor}&limit=1").json()
        self.assertTrue(page["has_more"])
        self.assertEqual([doc["id"] for doc in page["scopes"]["pickup"]["upserts"]], [first.id])

        rest = self._pull(token, f"?scopes=pickup&cursor={page['cursor']}&limit=1").json()
        self.assertEqual([doc["id"] for doc in rest["scopes"]["pickup"]["upserts"]], [second.id])
        self.assertFalse(rest["has_more"])


class TestSudiSyncPullEtag(SudiSyncApiCase):

    def test_an_unchanged_poll_costs_nothing(self):
        self._pending_receipt()
        token = self._login(device_uid="sync-op000030")["access_token"]
        cursor = self._pull(token, "?scopes=pickup").json()["cursor"]

        first = self._pull(token, f"?scopes=pickup&cursor={cursor}")
        etag = first.headers.get("ETag")
        self.assertTrue(etag, dict(first.headers))

        second = self._pull(
            token, f"?scopes=pickup&cursor={cursor}", headers={"If-None-Match": etag}
        )
        self.assertEqual(second.status_code, 304, second.text)
        self.assertEqual(second.content, b"")

    def test_a_change_invalidates_the_etag(self):
        receipt = self._pending_receipt()
        token = self._login(device_uid="sync-op000031")["access_token"]
        cursor = self._pull(token, "?scopes=pickup").json()["cursor"]
        etag = self._pull(token, f"?scopes=pickup&cursor={cursor}").headers["ETag"]

        receipt.sudi_internal_notes = "changed"
        self._sync_flush()

        response = self._pull(
            token, f"?scopes=pickup&cursor={cursor}", headers={"If-None-Match": etag}
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(
            [doc["id"] for doc in response.json()["scopes"]["pickup"]["upserts"]],
            [receipt.id],
        )

    def test_a_full_resync_is_never_cacheable(self):
        # A 304 in place of a full resync would leave a wiped phone empty.
        self._pending_receipt()
        token = self._login(device_uid="sync-op000032")["access_token"]
        response = self._pull(token, "?scopes=pickup")
        self.assertIsNone(response.headers.get("ETag"))


class TestSudiJangadPages(SudiSyncApiCase):

    def _png(self):
        import base64
        import io

        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (4, 4), (255, 255, 255)).save(buffer, format="PNG")
        return base64.b64encode(buffer.getvalue())

    def _fetch(self, token, picking_id, page):
        self.env.flush_all()
        return self.url_open(
            f"/api/field/v1/pickups/{picking_id}/jangad/{page}",
            headers={"Authorization": f"Bearer {token}"},
        )

    def test_page_zero_is_the_image_on_the_receipt(self):
        receipt = self._pending_receipt()
        receipt.sudi_jangad_image = self._png()
        self._sync_flush()
        token = self._login(device_uid="sync-op000040")["access_token"]

        response = self._fetch(token, receipt.id, 0)

        self.assertEqual(response.status_code, 200, response.text)
        self.assertTrue(response.content)
        self.assertEqual(response.headers["Cache-Control"], "private, max-age=86400")

    def test_later_pages_come_from_the_attachments(self):
        receipt = self._pending_receipt()
        receipt.sudo()._sudi_add_jangad_pages([self._png(), self._png()])
        self._sync_flush()
        token = self._login(device_uid="sync-op000041")["access_token"]

        self.assertEqual(self._fetch(token, receipt.id, 0).status_code, 200)
        self.assertEqual(self._fetch(token, receipt.id, 1).status_code, 200)
        self.assertEnvelope(self._fetch(token, receipt.id, 2), 404, "NOT_IN_SCOPE")

    def test_the_page_count_in_the_payload_matches_what_is_fetchable(self):
        receipt = self._pending_receipt()
        receipt.sudo()._sudi_add_jangad_pages([self._png(), self._png()])
        self._sync_flush()
        token = self._login(device_uid="sync-op000042")["access_token"]

        docs = self._pull(token, "?scopes=pickup").json()["scopes"]["pickup"]["upserts"]
        doc = [document for document in docs if document["id"] == receipt.id][0]

        self.assertEqual(doc["jangad_pages"], 2)
        for page in range(doc["jangad_pages"]):
            self.assertEqual(self._fetch(token, receipt.id, page).status_code, 200)

    def test_a_picking_that_is_not_job_work_cannot_be_fetched(self):
        # The same answer as a typo, so the route is not a way to probe for
        # records the caller was never shown.
        other = self.env["stock.picking"].create({
            "partner_id": self.partner.id,
            "picking_type_id": self.picking_type_in.id,
            "location_id": self.picking_type_in.default_location_src_id.id,
            "location_dest_id": self.picking_type_in.default_location_dest_id.id,
            "sudi_is_diamond_job_work": False,
        })
        token = self._login(device_uid="sync-op000043")["access_token"]

        self.assertEnvelope(self._fetch(token, other.id, 0), 404, "NOT_IN_SCOPE")

    def test_a_job_work_only_user_cannot_reach_a_pending_pickups_jangad(self):
        # A jangad page is reachable through the pickup and job-work scopes, so
        # the role that owns the scope is what decides, not the URL.
        receipt = self._pending_receipt()
        receipt.sudi_jangad_image = self._png()
        self._sync_flush()
        token = self._login(user=self.job_worker, device_uid="sync-jw000044")["access_token"]

        self.assertEnvelope(self._fetch(token, receipt.id, 0), 404, "NOT_IN_SCOPE")

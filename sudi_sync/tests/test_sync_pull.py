from .common import SudiSyncCase


class TestSudiSyncScopes(SudiSyncCase):
    """Each worklist holds what its role is supposed to see."""

    def _ids(self, result, scope):
        return [doc["id"] for doc in result["scopes"][scope]["upserts"]]

    def test_a_pending_receipt_is_in_the_pickup_scope(self):
        receipt = self._pending_receipt()
        self._sync_flush()
        result = self._pull(scopes=["pickup"])
        self.assertTrue(result["full_resync"])
        self.assertIn(receipt.id, self._ids(result, "pickup"))

    def test_the_pickup_document_carries_what_the_operator_needs(self):
        receipt = self._pending_receipt()
        self._sync_flush()
        doc = [
            document
            for document in self._pull(scopes=["pickup"])["scopes"]["pickup"]["upserts"]
            if document["id"] == receipt.id
        ][0]
        self.assertEqual(doc["name"], receipt.name)
        self.assertEqual(doc["customer"], {"id": self.partner.id, "name": self.partner.display_name})
        self.assertEqual(doc["contact_phone"], "9876543210")
        self.assertEqual(doc["pickup_address"], "12, Mahidharpura, Surat")
        # Naive UTC with a T, never a space: the client parses ISO.
        self.assertNotIn(" ", doc["created_at"])
        self.assertIn("T", doc["created_at"])

    def test_a_receipt_collected_by_someone_else_is_only_job_work(self):
        # _assigned_receipt carries a pickup time but no pickup person, so it
        # is nobody's "collected today".
        receipt = self._assigned_receipt()
        self._sync_flush()
        result = self._pull(scopes=["pickup", "jobwork"])
        self.assertNotIn(receipt.id, self._ids(result, "pickup"))
        self.assertIn(receipt.id, self._ids(result, "jobwork"))

    def test_a_receipt_i_collected_today_stays_on_my_pickup_list(self):
        """The operator has to be able to review their own round.

        Before this, confirming a pickup made the receipt vanish from the
        phone immediately: no way to check the jangad just collected, and no
        way to notice a parcel that was missed.
        """
        receipt = self._pending_receipt()
        receipt.with_user(self.operator).action_sudi_confirm_pickup()
        self._sync_flush()

        mine = self._pull(user=self.operator, scopes=["pickup"])

        docs = [
            doc for doc in mine["scopes"]["pickup"]["upserts"]
            if doc["id"] == receipt.id
        ]
        self.assertEqual(len(docs), 1)
        self.assertEqual(docs[0]["stage"], "collected")
        self.assertEqual(docs[0]["collected_by"]["id"], self.operator.id)
        self.assertTrue(docs[0]["collected_at"])

    def test_someone_elses_round_is_not_on_my_pickup_list(self):
        receipt = self._pending_receipt()
        receipt.with_user(self.operator_2).action_sudi_confirm_pickup()
        self._sync_flush()

        theirs = self._pull(user=self.operator, scopes=["pickup"])

        self.assertNotIn(receipt.id, self._ids(theirs, "pickup"))

    def test_a_pending_receipt_is_staged_as_awaiting(self):
        receipt = self._pending_receipt()
        self._sync_flush()
        docs = [
            doc for doc in self._pull(scopes=["pickup"])["scopes"]["pickup"]["upserts"]
            if doc["id"] == receipt.id
        ]
        self.assertEqual(docs[0]["stage"], "awaiting")
        self.assertIsNone(docs[0]["collected_at"])

    def test_a_job_work_document_carries_its_item_lines(self):
        receipt = self._assigned_receipt()
        self._sync_flush()
        doc = [
            document
            for document in self._pull(scopes=["jobwork"])["scopes"]["jobwork"]["upserts"]
            if document["id"] == receipt.id
        ][0]
        self.assertEqual(len(doc["items"]), 1)
        item = doc["items"][0]
        self.assertEqual(item["pcs"], 100.0)
        self.assertEqual(item["carats"], 25.0)
        self.assertEqual(item["job_type"]["id"], self.job_type.id)
        self.assertEqual(item["size"], "1.00 MM")
        self.assertFalse(doc["timer"]["running"])

    def test_a_new_delivery_is_awaiting_in_the_delivery_scope(self):
        delivery = self._create_receipt().sudi_delivery_ids
        self._sync_flush()
        docs = self._pull(scopes=["delivery"])["scopes"]["delivery"]["upserts"]
        doc = [document for document in docs if document["id"] == delivery.id][0]
        self.assertEqual(doc["stage"], "awaiting")
        self.assertIsNone(doc["taken_by"])
        self.assertEqual(doc["origin_receipt"]["id"], delivery.sudi_origin_receipt_id.id)

    def test_taking_a_delivery_keeps_it_in_scope_as_out(self):
        delivery = self._create_receipt().sudi_delivery_ids
        delivery.with_user(self.operator).action_sudi_take_for_delivery()
        self._sync_flush()
        docs = self._pull(scopes=["delivery"])["scopes"]["delivery"]["upserts"]
        doc = [document for document in docs if document["id"] == delivery.id][0]
        self.assertEqual(doc["stage"], "out")
        self.assertEqual(doc["taken_by"]["id"], self.operator.id)

    def test_a_delivery_completed_today_stays_with_the_person_who_did_it(self):
        delivery = self._create_receipt().sudi_delivery_ids
        delivery.with_user(self.operator).action_sudi_take_for_delivery()
        delivery.with_user(self.operator).action_sudi_mark_delivered()
        self._sync_flush()

        mine = self._pull(user=self.operator, scopes=["delivery"])
        self.assertIn(delivery.id, self._ids(mine, "delivery"))

        # Someone else's round is not on this operator's phone.
        theirs = self._pull(user=self.operator_2, scopes=["delivery"])
        self.assertNotIn(delivery.id, self._ids(theirs, "delivery"))


class TestSudiSyncDelta(SudiSyncCase):
    """Incremental pulls: upserts, scope exits, and the cursor."""

    def _ids(self, result, scope):
        return [doc["id"] for doc in result["scopes"][scope]["upserts"]]

    def test_an_unchanged_world_returns_nothing(self):
        self._pending_receipt()
        self._sync_flush()
        cursor = self._pull(scopes=["pickup"])["cursor"]

        result = self._pull(scopes=["pickup"], cursor=cursor)

        self.assertFalse(result["full_resync"])
        self.assertEqual(result["scopes"]["pickup"]["upserts"], [])
        self.assertEqual(result["scopes"]["pickup"]["gone"], [])

    def test_only_what_changed_comes_back(self):
        first = self._pending_receipt()
        self._pending_receipt()
        self._sync_flush()
        cursor = self._pull(scopes=["pickup"])["cursor"]

        first.sudi_internal_notes = "call before arriving"
        self._sync_flush()
        result = self._pull(scopes=["pickup"], cursor=cursor)

        self.assertEqual(self._ids(result, "pickup"), [first.id])
        self.assertGreater(result["cursor"], cursor)

    def test_a_record_leaving_a_scope_is_reported_as_gone(self):
        # Collected by somebody else, which is the real-world scope exit: a
        # receipt this operator collects themselves stays on their phone for
        # the rest of the day, so confirming it here would prove nothing.
        receipt = self._pending_receipt()
        self._sync_flush()
        cursor = self._pull(scopes=["pickup"])["cursor"]

        receipt.with_user(self.operator_2).action_sudi_confirm_pickup()
        self._sync_flush()
        result = self._pull(scopes=["pickup"], cursor=cursor)

        self.assertNotIn(receipt.id, self._ids(result, "pickup"))
        self.assertIn(receipt.id, result["scopes"]["pickup"]["gone"])

    def test_a_deleted_record_is_reported_as_gone(self):
        receipt = self._pending_receipt()
        self._sync_flush()
        cursor = self._pull(scopes=["pickup"])["cursor"]
        receipt_id = receipt.id

        receipt.unlink()
        self._sync_flush()
        result = self._pull(scopes=["pickup"], cursor=cursor)

        self.assertIn(receipt_id, result["scopes"]["pickup"]["gone"])

    def test_a_stale_cursor_forces_a_full_resync(self):
        self._pending_receipt()
        self._sync_flush()
        self._set_param("sudi_sync.pruned_through", "999999")

        result = self._pull(scopes=["pickup"], cursor=5)

        self.assertTrue(result["full_resync"])

    def test_a_delta_pages_by_advancing_the_cursor(self):
        first = self._pending_receipt()
        second = self._pending_receipt()
        self._sync_flush()
        cursor = self._pull(scopes=["pickup"])["cursor"]

        first.sudi_internal_notes = "one"
        self._sync_flush()
        second.sudi_internal_notes = "two"
        self._sync_flush()

        page = self._pull(scopes=["pickup"], cursor=cursor, limit=1)
        self.assertTrue(page["has_more"])
        self.assertEqual(self._ids(page, "pickup"), [first.id])

        rest = self._pull(scopes=["pickup"], cursor=page["cursor"], limit=1)
        self.assertEqual(self._ids(rest, "pickup"), [second.id])
        self.assertFalse(rest["has_more"])


class TestSudiSyncFullResyncPaging(SudiSyncCase):
    """A first sync on a fresh phone pages, and resumes where it stopped."""

    def _ids(self, result, scope):
        return [doc["id"] for doc in result["scopes"][scope]["upserts"]]

    def test_a_full_resync_pages_within_a_scope(self):
        first = self._pending_receipt()
        second = self._pending_receipt()
        self._sync_flush()

        page = self._pull(scopes=["pickup"], limit=1)
        self.assertTrue(page["full_resync"])
        self.assertTrue(page["has_more"])
        self.assertEqual(self._ids(page, "pickup"), [first.id])
        self.assertEqual(page["next_after_scope"], "pickup")
        self.assertEqual(page["next_after_id"], first.id)

        rest = self._pull(
            scopes=["pickup"],
            cursor=page["cursor"],
            limit=1,
            after_scope=page["next_after_scope"],
            after_id=page["next_after_id"],
        )
        self.assertTrue(rest["full_resync"])
        self.assertEqual(self._ids(rest, "pickup"), [second.id])
        self.assertFalse(rest["has_more"])

    def test_a_continuation_keeps_the_cursor_it_was_given(self):
        # Otherwise a change committed mid-paging would be skipped: the client
        # must end up with the cursor from page one.
        self._pending_receipt()
        self._pending_receipt()
        self._sync_flush()
        page = self._pull(scopes=["pickup"], limit=1)

        rest = self._pull(
            scopes=["pickup"],
            cursor=page["cursor"],
            limit=1,
            after_scope="pickup",
            after_id=page["next_after_id"],
        )
        self.assertEqual(rest["cursor"], page["cursor"])

    def test_scopes_already_delivered_come_back_empty_not_missing(self):
        self._pending_receipt()
        self._assigned_receipt()
        self._sync_flush()

        rest = self._pull(
            scopes=["pickup", "jobwork"], limit=50, after_scope="jobwork", after_id=0
        )
        self.assertIn("pickup", rest["scopes"])
        self.assertEqual(rest["scopes"]["pickup"]["upserts"], [])
        self.assertTrue(rest["scopes"]["jobwork"]["upserts"])

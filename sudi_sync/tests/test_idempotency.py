from odoo.tools import mute_logger

from ..models.sudi_idempotency import CLAIM_IN_PROGRESS, CLAIM_NEW, CLAIM_REPLAY
from .common import SudiSyncCase

ENDPOINT = "POST /deliveries/{id}/deliver"


class TestSudiIdempotency(SudiSyncCase):

    def setUp(self):
        super().setUp()
        self.Keys = self.env["sudi.idempotency.key"]

    def test_a_fresh_key_is_the_callers_to_do(self):
        state, claim = self.Keys._sudi_claim(self.operator, ENDPOINT, "key-1")
        self.assertEqual(state, CLAIM_NEW)
        self.assertTrue(claim)

    def test_a_stored_answer_is_replayed_verbatim(self):
        state, claim = self.Keys._sudi_claim(self.operator, ENDPOINT, "key-1")
        claim._sudi_store({"delivered": True, "id": 42})

        state, response = self.Keys._sudi_claim(self.operator, ENDPOINT, "key-1")

        self.assertEqual(state, CLAIM_REPLAY)
        self.assertEqual(response, {"delivered": True, "id": 42})

    def test_a_key_claimed_but_unanswered_is_in_progress(self):
        # The winner's transaction has not committed, so there is genuinely
        # nothing to replay. "Retry" is the only honest answer.
        self.Keys._sudi_claim(self.operator, ENDPOINT, "key-1")
        state, response = self.Keys._sudi_claim(self.operator, ENDPOINT, "key-1")
        self.assertEqual(state, CLAIM_IN_PROGRESS)
        self.assertIsNone(response)

    def test_the_same_key_on_another_endpoint_is_independent(self):
        state, claim = self.Keys._sudi_claim(self.operator, ENDPOINT, "key-1")
        claim._sudi_store({"a": 1})
        state, claim = self.Keys._sudi_claim(self.operator, "POST /pickups/1/confirm", "key-1")
        self.assertEqual(state, CLAIM_NEW)

    def test_the_same_key_from_another_user_is_independent(self):
        state, claim = self.Keys._sudi_claim(self.operator, ENDPOINT, "key-1")
        claim._sudi_store({"a": 1})
        state, claim = self.Keys._sudi_claim(self.operator_2, ENDPOINT, "key-1")
        self.assertEqual(state, CLAIM_NEW)

    def test_no_key_means_no_deduplication(self):
        # Lets a router treat the header as optional without branching.
        state, claim = self.Keys._sudi_claim(self.operator, ENDPOINT, None)
        self.assertEqual(state, CLAIM_NEW)
        self.assertIsNone(claim)

    def test_a_race_defers_to_the_winner(self):
        # The loser hits the unique index rather than a Python check, which is
        # the whole point: two requests would both pass a check-then-insert.
        winner = self.Keys.sudo().create({
            "user_id": self.operator.id,
            "endpoint": ENDPOINT,
            "key": "key-race",
        })
        winner._sudi_store({"from": "the winner"})

        state, response = self.Keys._sudi_claim(self.operator, ENDPOINT, "key-race")

        self.assertEqual(state, CLAIM_REPLAY)
        self.assertEqual(response, {"from": "the winner"})

    def test_a_claim_can_remember_the_record_it_produced(self):
        receipt = self._pending_receipt()
        state, claim = self.Keys._sudi_claim(self.operator, ENDPOINT, "key-1")
        claim._sudi_store({"ok": True}, record=receipt)
        self.assertEqual(claim.result_model, "stock.picking")
        self.assertEqual(claim.result_id, receipt.id)

    @mute_logger("odoo.sql_db")
    def test_the_unique_index_is_real(self):
        from psycopg2 import IntegrityError
        self.Keys.sudo().create({
            "user_id": self.operator.id,
            "endpoint": ENDPOINT,
            "key": "key-1",
        })
        with self.assertRaises(IntegrityError):
            with self.env.cr.savepoint():
                self.Keys.sudo().create({
                    "user_id": self.operator.id,
                    "endpoint": ENDPOINT,
                    "key": "key-1",
                })

from odoo.tests.common import tagged

from .test_jangad import SudiJangadCase


@tagged("post_install", "-at_install")
class TestSudiCustomerServices(SudiJangadCase):
    """The dashboard: pick services, then send the jangad with them."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.job_type = cls.env.ref("diamond.job_type_laser_inscription")
        cls.carat_type = cls.env["sudi.diamond.job.type"].sudo().create({
            "name": "Customer Carat Grading",
            "service_product_id": cls.job_type.service_product_id.id,
            "invoice_basis": "carats",
            "invoice_description": "Grading, billed per carat",
        })

    def test_the_dashboard_lists_the_active_services(self):
        token = self._customer()
        response = self._get("/services", token)
        self.assertEqual(response.status_code, 200, response.text)
        by_id = {service["id"]: service for service in response.json()}
        self.assertEqual(by_id[self.carat_type.id]["unit"], "ct")
        self.assertEqual(by_id[self.carat_type.id]["description"], "Grading, billed per carat")
        self.assertEqual(by_id[self.job_type.id]["unit"], "pcs")
        # Prices are between the customer and the office.
        self.assertNotIn("price", by_id[self.job_type.id])

    def test_an_unchanged_dashboard_is_a_304(self):
        token = self._customer()
        etag = self._get("/services", token).headers["ETag"]
        self.env.flush_all()
        again = self.url_open(
            "/api/customer/v1/services",
            headers={"Authorization": f"Bearer {token}", "If-None-Match": etag},
        )
        self.assertEqual(again.status_code, 304)

    def test_staff_cannot_read_the_customer_dashboard(self):
        staff = self._login(device_uid="svc-staff0001")["access_token"]
        self.assertEnvelope(self._get("/services", staff), 401, "AUTH")

    def test_a_jangad_carries_the_services_picked(self):
        token = self._customer()
        reference = self._stage(token)

        response = self._submit(token, {
            "upload_ids": [reference],
            "manual_pickup_address": "12, Mahidharpura",
            "services": [
                {"job_type_id": self.job_type.id, "quantity": 120, "remarks": "big first"},
                {"job_type_id": self.carat_type.id, "quantity": 2.5},
            ],
        })

        self.assertEqual(response.status_code, 200, response.text)
        receipt = self._receipt(response.json()["reference"])
        self.assertEqual(receipt.sudi_jangad_source, "customer_app")
        self.assertFalse(receipt.sudi_posted_by_id)
        requests = receipt.sudi_service_request_ids
        self.assertEqual(requests.job_type_id, self.job_type | self.carat_type)
        self.assertEqual(requests.mapped("unit"), ["pcs", "ct"])
        self.assertEqual(requests[0].remarks, "big first")

    def test_a_jangad_without_services_is_still_accepted(self):
        token = self._customer()
        reference = self._stage(token)
        response = self._submit(token, {
            "upload_ids": [reference], "manual_pickup_address": "12, Mahidharpura",
        })
        self.assertEqual(response.status_code, 200, response.text)
        receipt = self._receipt(response.json()["reference"])
        self.assertFalse(receipt.sudi_service_request_ids)
        self.assertEqual(receipt.sudi_jangad_source, "customer_app")

    def test_a_retired_service_refuses_the_jangad_and_files_nothing(self):
        token = self._customer()
        reference = self._stage(token)
        self.carat_type.sudo().active = False

        response = self._submit(token, {
            "upload_ids": [reference],
            "manual_pickup_address": "12, Mahidharpura",
            "services": [{"job_type_id": self.carat_type.id, "quantity": 1}],
        })

        self.assertEnvelope(response, 422, "VALIDATION")
        self.env.invalidate_all()
        upload = self.env["sudi.upload"].sudo().search([("reference", "=", reference)])
        self.assertFalse(upload.consumed_at)

    def test_a_negative_quantity_is_a_validation_error(self):
        token = self._customer()
        reference = self._stage(token)
        response = self._submit(token, {
            "upload_ids": [reference],
            "manual_pickup_address": "12, Mahidharpura",
            "services": [{"job_type_id": self.job_type.id, "quantity": -1}],
        })
        self.assertEnvelope(response, 422, "VALIDATION")

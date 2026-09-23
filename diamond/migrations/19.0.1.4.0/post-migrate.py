from odoo import SUPERUSER_ID, api


def migrate(cr, version):
    """Give today's Onfield users the new Job Work User role.

    Job work and delivery used to share one group, so every Onfield user could
    open the Job Work app. Splitting the roles would silently take that menu
    away from people who use it daily, so membership is carried over here and
    the operations team can trim it afterwards.
    """
    env = api.Environment(cr, SUPERUSER_ID, {})
    operator = env.ref(
        "diamond.group_sudi_pickup_delivery_operator", raise_if_not_found=False
    )
    job_work = env.ref("diamond.group_sudi_job_work_user", raise_if_not_found=False)
    if not operator or not job_work:
        return
    users = operator.all_user_ids - job_work.all_user_ids
    if users:
        job_work.write({"user_ids": [(4, user.id) for user in users]})

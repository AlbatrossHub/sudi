"""Phase 'owa' (the WhatsApp connector account + chatbot/auto-reply config) and phase
'discuss' (SQL lane: the 331 channels — 276 of them WhatsApp chats — and their members).
Channels are copied by SQL so every connector column (assignee, whatsapp state, kinds)
arrives byte-identical; message links are re-pointed after the chatter phase."""
import logging

from ..core import OrmStep, FuncStep, TableCopy, SqlStep, IDM
from .. import config

_logger = logging.getLogger("etl.discuss")
S = config.SRC_SCHEMA
COMPANY = "(SELECT id FROM res_company ORDER BY id LIMIT 1)"


def _owa_account_transform(vals, row, ctx):
    if not config.PRODUCTION:   # dry runs never talk to the live sidecar / Meta cloud
        vals.update({"enabled": False, "is_listening": False, "sidecar_url": "http://dry-run-disabled",
                     "sidecar_api_key": "dry-run-disabled", "webhook_secret": "dry-run-disabled",
                     "cloud_app_secret": False, "cloud_access_token": False, "cloud_verify_token": False,
                     "qr_code_base64": False})
    vals.pop("session_state", None)
    return vals


OWA_ACCOUNT = OrmStep("owa.account", key=["name"], transform=_owa_account_transform,
                      skip={"qr_code_base64", "health_status", "throttle_backoff_until", "last_seen_dt",
                            "import_prompt_pending"})

OWA_ACCOUNT_RELS = SqlStep("owa.account.rels", f"""
    INSERT INTO owa_account_res_users_rel (owa_account_id, res_users_id)
    SELECT a.new_id, u.new_id FROM {{S}}.owa_account_res_users_rel r
    JOIN etl_idmap a ON a.model = 'owa.account' AND a.old_id = r.owa_account_id
    JOIN etl_idmap u ON u.model = 'res.users' AND u.old_id = r.res_users_id
    ON CONFLICT DO NOTHING;
    INSERT INTO owa_account_res_company_rel (owa_account_id, res_company_id)
    SELECT a.new_id, {COMPANY} FROM {{S}}.owa_account_res_company_rel r
    JOIN etl_idmap a ON a.model = 'owa.account' AND a.old_id = r.owa_account_id
    ON CONFLICT DO NOTHING""")

_acc = IDM("ac", "owa.account", "s.wa_account_id")

CHATBOTS = TableCopy(   # chatbots first (their step pointers linked after the steps exist)
    "owa.chatbot", "owa_chatbot", joins=[_acc],
    overrides={"id": "s.id + {OFF}", "wa_account_id": "ac.new_id",
               "fallback_step_id": "NULL", "first_step_id": "NULL"},
    register=("owa.chatbot", "owa_chatbot"),
)
CHATBOT_STEPS_ = TableCopy(
    "owa.chatbot.step", "owa_chatbot_step",
    joins=[IDM("hu", "res.users", "s.handoff_user_id"), IDM("tu", "res.users", "s.target_user_id")],
    overrides={"id": "s.id + {OFF}", "chatbot_id": "s.chatbot_id + {OFF}", "next_step_id": "NULL",
               "handoff_user_id": "hu.new_id", "target_user_id": "tu.new_id", "handoff_team_id": "NULL"},
    register=("owa.chatbot.step", "owa_chatbot_step"),
)
CHATBOT_LINKS = SqlStep("owa.chatbot.links", f"""
    UPDATE owa_chatbot t SET first_step_id = s.first_step_id + {{OFF}},
                             fallback_step_id = CASE WHEN s.fallback_step_id IS NOT NULL THEN s.fallback_step_id + {{OFF}} END
    FROM {{S}}.owa_chatbot s WHERE t.id = s.id + {{OFF}};
    UPDATE owa_chatbot_step t SET next_step_id = s.next_step_id + {{OFF}}
    FROM {{S}}.owa_chatbot_step s WHERE t.id = s.id + {{OFF}} AND s.next_step_id IS NOT NULL;
    INSERT INTO owa_chatbot_menu_option (id, step_id, next_step_id, sequence, keyword, label, create_uid, write_uid, create_date, write_date)
    SELECT s.id + {{OFF}}, s.step_id + {{OFF}}, s.next_step_id + {{OFF}}, s.sequence, s.keyword, s.label,
           1, 1, s.create_date, s.write_date
    FROM {{S}}.owa_chatbot_menu_option s ON CONFLICT DO NOTHING""")

AUTO_REPLIES = TableCopy(
    "owa.auto.reply", "owa_auto_reply", joins=[_acc],
    overrides={"id": "s.id + {OFF}", "wa_account_id": "ac.new_id"},
    register=("owa.auto.reply", "owa_auto_reply"),
)

# module records referenced by connector rows (quick replies, reports) map by xmlid
def _XMLID(alias, model, expr):
    return (f"LEFT JOIN {{S}}.ir_model_data s{alias} ON s{alias}.model = '{model}' AND s{alias}.res_id = {expr} "
            f"LEFT JOIN ir_model_data {alias} ON {alias}.model = '{model}' AND {alias}.module = s{alias}.module "
            f"AND {alias}.name = s{alias}.name")


# The connector's post_init_hook seeds these only for models installed BEFORE it; on a fresh 20
# it installs ahead of account/stock/project and seeds none, so prod's rows (with the user's
# on/off choices) are copied. Model/field ids resolve by name: chatter.maps has not run yet.
NOTIFICATION_RULES = TableCopy(
    "owa.notification.rule", "owa_notification_rule",
    joins=[_acc, "LEFT JOIN {S}.ir_model sm ON sm.id = s.model_id", "LEFT JOIN ir_model im ON im.model = sm.model",
           "LEFT JOIN {S}.ir_model_fields sf ON sf.id = s.trigger_field_id",
           "LEFT JOIN ir_model_fields tf ON tf.model = sf.model AND tf.name = sf.name",
           _XMLID("qr", "owa.quick.reply", "s.quick_reply_id"), _XMLID("rp", "ir.actions.report", "s.report_id")],
    where="im.id IS NOT NULL",
    overrides={"id": "s.id + {OFF}", "wa_account_id": "ac.new_id", "model_id": "im.id", "trigger_field_id": "tf.id",
               "quick_reply_id": "qr.res_id", "report_id": "rp.res_id"},
    register=("owa.notification.rule", "owa_notification_rule"),
)

# inbound message ids already processed: without them a webhook the sidecar replays after the
# switch-over would land the same customer message twice
INBOUND_DEDUPE = TableCopy(
    "owa.inbound.dedupe", "owa_inbound_dedupe", joins=[IDM("ac", "owa.account", "s.account_id")],
    overrides={"id": "s.id + {OFF}", "account_id": "ac.new_id"}, on_conflict="DO NOTHING",
)

OWA_STEPS = [OWA_ACCOUNT, OWA_ACCOUNT_RELS, CHATBOTS, CHATBOT_STEPS_, CHATBOT_LINKS, AUTO_REPLIES,
             NOTIFICATION_RULES, INBOUND_DEDUPE]

# ----------------------------------------------------------------------------- channels (SQL lane)
# member_indices is frozen at creation on 20 ("p<pid>,p<pid>" over NEW partner ids) and its
# CHECK constraint demands it on chats at insert time. Chats whose 2 members did not both map
# become groups (marut rule; 2 such chats on prod).
_MI = ("LEFT JOIN LATERAL (SELECT string_agg('p' || pm.new_id, ',' ORDER BY pm.new_id) AS mi, count(*) AS n "
       "FROM {S}.discuss_channel_member scm "
       "JOIN etl_idmap pm ON pm.model = 'res.partner' AND pm.old_id = scm.partner_id "
       "WHERE scm.channel_id = s.id AND scm.partner_id IS NOT NULL) mix ON true")
CHANNELS = TableCopy(
    "discuss.channel", "discuss_channel",
    joins=[IDM("au", "res.users", "s.assignee_id"), IDM("gp", "res.groups", "s.group_public_id"),
           IDM("wp", "res.partner", "s.whatsapp_partner_id"), IDM("ac2", "owa.account", "s.owa_account_id"), _MI],
    overrides={"id": "s.id + {OFF}", "assignee_id": "au.new_id", "group_public_id": "gp.new_id",
               "whatsapp_partner_id": "wp.new_id", "owa_account_id": "ac2.new_id",
               "parent_channel_id": "NULL", "from_message_id": "NULL", "last_wa_mail_message_id": "NULL",
               "channel_type": "CASE WHEN s.channel_type = 'chat' AND coalesce(mix.n, 0) < 2 THEN 'group' "
                               "ELSE s.channel_type END"},
    extra={"member_indices": "CASE WHEN s.channel_type = 'chat' AND coalesce(mix.n, 0) >= 2 THEN mix.mi END"},
    register=("discuss.channel", "discuss_channel"), chunk=20000,
)
CHANNEL_LINKS = SqlStep("discuss.channel.links", f"""
    UPDATE discuss_channel t SET parent_channel_id = s.parent_channel_id + {{OFF}}
    FROM {{S}}.discuss_channel s JOIN discuss_channel p ON p.id = s.parent_channel_id + {{OFF}}
    WHERE t.id = s.id + {{OFF}} AND s.parent_channel_id IS NOT NULL""")

MEMBERS = TableCopy(
    "discuss.channel.member", "discuss_channel_member",
    joins=["JOIN discuss_channel ch ON ch.id = s.channel_id + {OFF}", IDM("pa", "res.partner", "s.partner_id")],
    where="s.guest_id IS NULL AND (s.partner_id IS NULL OR pa.new_id IS NOT NULL)",
    overrides={"id": "s.id + {OFF}", "channel_id": "ch.id", "partner_id": "pa.new_id", "guest_id": "NULL",
               "fetched_message_id": "NULL", "seen_message_id": "NULL", "rtc_inviting_session_id": "NULL"},
    register=("discuss.channel.member", "discuss_channel_member"), chunk=50000,
)

CALL_LOGS = TableCopy(
    "owa.call.log", "owa_call_log",
    joins=[_acc, IDM("pa", "res.partner", "s.partner_id"), "LEFT JOIN discuss_channel ch ON ch.id = s.channel_id + {OFF}"],
    overrides={"id": "s.id + {OFF}", "wa_account_id": "ac.new_id", "partner_id": "pa.new_id",
               "channel_id": "ch.id", "company_id": COMPANY},
    register=("owa.call.log", "owa_call_log"),
)


def _recompute_channels(engine, ctx):
    """member_indices & co are stored computes maintained by the ORM — fill them for the copied rows,
    then re-point seen/fetched markers is skipped (ephemeral read-state, rules doc §B)."""
    env, cr = engine.env, engine.cr
    env.invalidate_all()
    cr.execute(f"SELECT id + {config.OFFSET} FROM {S}.discuss_channel")
    channels = env["discuss.channel"].browse([r[0] for r in cr.fetchall()]).exists()
    C = env["discuss.channel"]
    for f in ("member_indices", "member_count"):
        if f in C._fields and C._fields[f].store and C._fields[f].compute:
            env.add_to_compute(C._fields[f], channels)
    C.flush_model()
    for t in ("discuss_channel", "discuss_channel_member", "owa_call_log", "owa_chatbot", "owa_chatbot_step",
              "owa_chatbot_menu_option", "owa_auto_reply"):
        cr.execute(f"SELECT setval(pg_get_serial_sequence('{t}', 'id'), GREATEST((SELECT coalesce(max(id), 1) FROM {t}), 1))")
    return dict(channels=len(channels))


RECOMPUTE = FuncStep("discuss.recompute", _recompute_channels)

# after chatter: message-flavoured channel links
CHANNEL_MSG_LINKS = SqlStep("discuss.channel.msg_links", f"""
    UPDATE discuss_channel t SET from_message_id = s.from_message_id + {{OFF}}
    FROM {{S}}.discuss_channel s JOIN mail_message m ON m.id = s.from_message_id + {{OFF}}
    WHERE t.id = s.id + {{OFF}} AND s.from_message_id IS NOT NULL;
    UPDATE discuss_channel t SET last_wa_mail_message_id = s.last_wa_mail_message_id + {{OFF}}
    FROM {{S}}.discuss_channel s JOIN mail_message m ON m.id = s.last_wa_mail_message_id + {{OFF}}
    WHERE t.id = s.id + {{OFF}} AND s.last_wa_mail_message_id IS NOT NULL""")

# group-restricted channels (prod's "Administrators"): raw group ids cannot cross DBs,
# so the copy left group_public_id NULL and every internal user could see the channel.
# Restore the restriction by matching the group's xmlid (found by the per-user QA).
CHANNEL_GROUPS = SqlStep("discuss.channel.group_public", """
    UPDATE discuss_channel c SET group_public_id = tgt.res_id
    FROM etl_idmap m
    JOIN {S}.discuss_channel s ON s.id = m.old_id
    JOIN {S}.ir_model_data sd ON sd.model = 'res.groups' AND sd.res_id = s.group_public_id
    JOIN ir_model_data tgt ON tgt.model = 'res.groups' AND tgt.module = sd.module AND tgt.name = sd.name
    WHERE m.model = 'discuss.channel' AND c.id = m.new_id
      AND s.group_public_id IS NOT NULL AND c.group_public_id IS NULL""")

# auto-join groups (prod's General -> employees, Administrators -> settings group), by xmlid
CHANNEL_AUTO_GROUPS = SqlStep("discuss.channel.auto_groups", """
    INSERT INTO discuss_channel_res_groups_rel (discuss_channel_id, res_groups_id)
    SELECT c.id, tgt.res_id FROM {S}.discuss_channel_res_groups_rel r
    JOIN discuss_channel c ON c.id = r.discuss_channel_id + {OFF}
    JOIN {S}.ir_model_data sd ON sd.model = 'res.groups' AND sd.res_id = r.res_groups_id
    JOIN ir_model_data tgt ON tgt.model = 'res.groups' AND tgt.module = sd.module AND tgt.name = sd.name
    ON CONFLICT DO NOTHING""")

# state of a WhatsApp group chat (one row per group channel)
GROUP_SESSIONS = TableCopy(
    "owa.group.session", "owa_group_session",
    joins=[_acc, "JOIN discuss_channel ch ON ch.id = s.channel_id + {OFF}"],
    overrides={"id": "s.id + {OFF}", "channel_id": "ch.id", "wa_account_id": "ac.new_id", "company_id": COMPANY},
    on_conflict="DO NOTHING",
)

STEPS = [CHANNELS, CHANNEL_LINKS, MEMBERS, CALL_LOGS, CHANNEL_GROUPS, CHANNEL_AUTO_GROUPS, GROUP_SESSIONS, RECOMPUTE]

# after chatter: the connector's per-message record (delivery ticks, Meta message id, reactions)
# sits beside the chatter message it was sent as. Not in the September source; found by the
# full-table sweep of the prod pull (1,511 rows).
OWA_MESSAGES = TableCopy(
    "owa.message", "owa_message",
    joins=[_acc, IDM("wp", "res.partner", "s.whatsapp_partner_id"),
           "LEFT JOIN mail_message mm ON mm.id = s.mail_message_id + {OFF}",
           _XMLID("qr", "owa.quick.reply", "s.quick_reply_id"), _XMLID("rp", "ir.actions.report", "s.pending_report_id")],
    overrides={"id": "s.id + {OFF}", "wa_account_id": "ac.new_id", "whatsapp_partner_id": "wp.new_id",
               "mail_message_id": "mm.id", "parent_id": "NULL", "campaign_id": "NULL",
               "quick_reply_id": "qr.res_id", "pending_report_id": "rp.res_id"},
    register=("owa.message", "owa_message"), chunk=50000, on_conflict="DO NOTHING",
)
OWA_MESSAGE_PARENTS = SqlStep("owa.message.parents", f"""
    UPDATE owa_message t SET parent_id = s.parent_id + {{OFF}}
    FROM {{S}}.owa_message s JOIN owa_message p ON p.id = s.parent_id + {{OFF}}
    WHERE t.id = s.id + {{OFF}} AND s.parent_id IS NOT NULL""")

POST_CHATTER_STEPS = [CHANNEL_MSG_LINKS, OWA_MESSAGES, OWA_MESSAGE_PARENTS]

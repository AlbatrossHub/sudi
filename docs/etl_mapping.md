# ETL mapping workbench: sudi_src19 (Odoo 18) → sudi20_ref (Odoo 20)

Generated 2026-09-28T16:12:46.250923Z by `tools/etl/workbench_columns.py`. Every row below needs a rule before the first dry run; `REVIEW` rows are the open items. Raw data: `tools/etl/workbench/columns.json`.

Tables with data on 18: **251**, present on 20: **240**, absent on 20: **11**. Dropped columns: **109** (62 to review, 42 empty). Type changes: 12. Selection keys in use that vanished: 4. New NOT NULL columns without default: 21.


## A. Tables with data on 18 that do not exist on 20

| table | rows | model / m2m of | 18 module | rule |
|---|---:|---|---|---|
| `mail_tracking_value` | 6,984 | mail.tracking.value | account | REVIEW |
| `ir_model_access` | 990 | ir.model.access | base | REVIEW |
| `payment_method_payment_provider_rel` | 306 | m2m: payment.method.provider_ids, payment.provider.payment_method_ids |  | REVIEW m2m: find the 20 relation table of payment.method.provider_ids, payment.provider.payment_method_ids |
| `ir_rule` | 295 | ir.rule | base | REVIEW |
| `rule_group_rel` | 198 | m2m: ir.rule.groups, res.groups.rule_groups |  | REVIEW m2m: find the 20 relation table of ir.rule.groups, res.groups.rule_groups |
| `l10n_in_section_alert` | 87 | l10n_in.section.alert | l10n_in | REVIEW |
| `hr_timesheet_stop_timer_confirmation_wizard` | 13 | hr.timesheet.stop.timer.confirmation.wizard | timesheet_grid | REVIEW |
| `hr_contract_type` | 12 | hr.contract.type | hr | REVIEW |
| `mail_message_res_partner_starred_rel` | 1 | m2m: mail.message.starred_partner_ids |  | DROPPED BY DECISION |
| `res_bank` | 1 | res.bank | base | REVIEW |
| `whatsapp_template` | 1 | ? |  | REVIEW |

## B. Columns present on 18 and gone on 20 (tables that exist on both)

Rows with data and no known rule are marked REVIEW; `samples` are the three most frequent values.

| table | rows | column | type | non-null | 18 module | rule | samples |
|---|---:|---|---|---:|---|---|---|
| `stock_move_line` | 3,657 | `product_uom_id` | many2one → uom.uom | 3,657 | stock | REVIEW | 1 ×3657 |
| `discuss_channel_member` | 727 | `fetched_message_id` | many2one → mail.message | 418 | mail | REVIEW | 1 ×16; 2395 ×5; 2404 ×5 |
| `account_move_line` | 371 | `deductible_amount` | float | 371 | account | REVIEW | 100.0 ×371 |
| `res_country` | 251 | `zip_required` | boolean | 251 | base | REVIEW | True ×241; False ×10 |
| `account_tax` | 428 | `l10n_in_section_id` | many2one → l10n_in.section.alert | 207 | l10n_in | REVIEW | 74 ×8; 68 ×6; 51 ×6 |
| `account_move_line` | 371 | `sudi_stock_move_id` | many2one → stock.move | 190 | diamond | REVIEW | 1521 ×1; 184 ×1; 384 ×1 |
| `account_move_line` | 371 | `sudi_billing_line_id` | many2one → sudi.diamond.billing.line | 190 | diamond | REVIEW | 116 ×1; 87 ×1; 184 ×1 |
| `sudi_diamond_billing_line` | 1,883 | `invoice_line_id` | many2one → account.move.line | 190 | diamond | REVIEW | 22 ×1; 23 ×1; 18 ×1 |
| `account_account` | 110 | `create_asset` | selection | 110 | account_asset | REVIEW | no ×110 |
| `account_account` | 110 | `multiple_assets_per_line` | boolean | 110 | account_asset | REVIEW | False ×110 |
| `account_move` | 62 | `checked` | boolean | 62 | account | REVIEW | False ×62 |
| `account_return_check_template` | 51 | `cycle` | selection | 51 | account_reports | REVIEW | treasury_financing ×16; equity ×8; state ×5 |
| `ir_act_report_xml` | 48 | `report_file` | character varying | 47 |  | REVIEW | stock.report_package_barcode ×4; stock.label_package_template_view ×2; report_timesheet ×2 |
| `account_report` | 27 | `prefix_groups_threshold` | integer | 27 | account | REVIEW | 4000 ×27 |
| `account_report` | 27 | `active` | boolean | 27 | account | REVIEW | True ×27 |
| `account_report` | 27 | `filter_analytic` | boolean | 27 | account | REVIEW | False ×23; True ×4 |
| `account_report_line` | 184 | `foldable` | boolean | 23 | account | REVIEW | True ×22; False ×1 |
| `payment_provider` | 23 | `color` | integer | 23 | payment | REVIEW | 4 ×23 |
| `payment_provider` | 23 | `state` | selection | 23 | payment | REVIEW | disabled ×23 |
| `product_template` | 19 | `sale_delay` | integer | 19 | stock | REVIEW | 0 ×19 |
| `res_users_settings` | 17 | `voice_active_duration` | integer | 17 | mail | REVIEW | 200 ×17 |
| `res_users_settings` | 17 | `is_discuss_sidebar_category_channel_open` | boolean | 17 | mail | REVIEW | True ×17 |
| `res_users_settings` | 17 | `is_discuss_sidebar_category_chat_open` | boolean | 17 | mail | REVIEW | True ×17 |
| `res_users_settings` | 17 | `use_push_to_talk` | boolean | 16 | mail | REVIEW | False ×16 |
| `resource_calendar_attendance` | 15 | `name` | char | 15 | resource | REVIEW | Monday Morning ×1; Friday Lunch ×1; Wednesday Afternoon ×1 |
| `resource_calendar_attendance` | 15 | `duration_days` | float | 15 | resource | REVIEW | 0.5 ×10; 0.0 ×5 |
| `account_tax_group` | 12 | `tax_payable_account_id` | many2one → account.account | 12 | account | REVIEW | 35 ×12 |
| `account_tax_group` | 12 | `tax_receivable_account_id` | many2one → account.account | 12 | account | REVIEW | 11 ×12 |
| `hr_version` | 12 | `employee_type` | selection | 12 | hr | REVIEW | employee ×12 |
| `hr_version` | 12 | `is_flexible` | boolean | 12 | hr | REVIEW | False ×12 |
| `hr_version` | 12 | `is_fully_flexible` | boolean | 12 | hr | REVIEW | False ×12 |
| `website_menu` | 9 | `url` | char | 9 | website | REVIEW | # ×2; / ×2; /contactus ×2 |
| `stock_picking_type` | 8 | `sequence_code` | char | 8 | stock | REVIEW | QC ×1; OUT ×1; IN ×1 |
| `stock_picking_type` | 8 | `show_operations` | boolean | 8 | stock | REVIEW | False ×8 |
| `mail_activity_type` | 7 | `chaining_type` | selection | 7 | mail | REVIEW | suggest ×7 |
| `account_account` | 110 | `l10n_in_tds_tcs_section_id` | many2one → l10n_in.section.alert | 5 | l10n_in | REVIEW | 18 ×2; 22 ×2; 3 ×1 |
| `product_category` | 3 | `complete_name` | char | 3 | product | REVIEW | Goods ×1; Services ×1; Expenses ×1 |
| `account_followup_followup_line` | 2 | `name` | char | 2 | account_followup | REVIEW | {'en_US': '30 Days'} ×1; {'en_US': '15 Days'} ×1 |
| `account_followup_followup_line` | 2 | `join_invoices` | boolean | 2 | account_followup | REVIEW | True ×2 |
| `account_followup_followup_line` | 2 | `auto_execute` | boolean | 2 | account_followup | REVIEW | False ×2 |
| `discuss_channel_member` | 727 | `custom_channel_name` | char | 1 | mail | REVIEW |  ×1 |
| `mail_activity` | 6 | `previous_activity_type_id` | many2one → mail.activity.type | 1 | mail | REVIEW | 2 ×1 |
| `res_company` | 1 | `layout_background` | selection | 1 | base | REVIEW | Blank ×1 |
| `res_company` | 1 | `logo_web` | binary | 1 | base | REVIEW |  |
| `res_company` | 1 | `income_account_id` | many2one → account.account | 1 | account | REVIEW | 39 ×1 |
| `res_company` | 1 | `expense_account_id` | many2one → account.account | 1 | account | REVIEW | 55 ×1 |
| `res_company` | 1 | `inventory_valuation` | selection | 1 | stock_account | REVIEW | periodic ×1 |
| `res_company` | 1 | `cost_method` | selection | 1 | stock_account | REVIEW | standard ×1 |
| `res_company` | 1 | `l10n_in_withholding_account_id` | many2one → account.account | 1 | l10n_in | REVIEW | 97 ×1 |
| `res_company` | 1 | `l10n_in_is_gst_registered` | boolean | 1 | l10n_in | REVIEW | True ×1 |
| `resource_calendar` | 1 | `schedule_type` | selection | 1 | resource | REVIEW | fully_fixed ×1 |
| `resource_calendar` | 1 | `tz` | selection | 1 | resource | REVIEW | Asia/Kolkata ×1 |
| `resource_calendar` | 1 | `flexible_hours` | boolean | 1 | resource | REVIEW | False ×1 |
| `resource_calendar` | 1 | `two_weeks_calendar` | boolean | 1 | resource | REVIEW | False ×1 |
| `website` | 1 | `social_twitter` | char | 1 | website | REVIEW | https://x.com/SDPPL15 ×1 |
| `website` | 1 | `social_facebook` | char | 1 | website | REVIEW | https://www.facebook.com/sdppl ×1 |
| `website` | 1 | `social_github` | char | 1 | website | REVIEW |  ×1 |
| `website` | 1 | `social_linkedin` | char | 1 | website | REVIEW | https://www.linkedin.com/company/sdppl/ ×1 |
| `website` | 1 | `social_youtube` | char | 1 | website | REVIEW |  ×1 |
| `website` | 1 | `social_instagram` | char | 1 | website | REVIEW | https://www.instagram.com/sdppl15/ ×1 |
| `website` | 1 | `social_tiktok` | char | 1 | website | REVIEW |  ×1 |
| `website` | 1 | `social_discord` | char | 1 | website | REVIEW |  ×1 |
| `stock_move` | 3,640 | `product_uom` | many2one → uom.uom | 3,640 | stock | RENAME -> uom_id |  |
| `res_device_log` | 1,878 | `platform` | char | 1,878 | base | DROPPED BY DECISION |  |
| `res_device_log` | 1,878 | `device_type` | selection | 1,878 | base | DROPPED BY DECISION |  |
| `res_device_log` | 1,878 | `browser` | char | 1,869 | base | DROPPED BY DECISION |  |
| `res_partner` | 494 | `vies_valid` | boolean | 494 | base_vat | DROP (base_vat merged into account; recheck on 20) |  |
| `res_partner` | 494 | `global_location_number` | char | 0 | account_add_gln | DROP (account_add_gln merged; GLN unused on prod) |  |
| `res_partner` | 494 | `peppol_endpoint` | char | 0 | account_edi_ubl_cii | RENAME -> routing_endpoint (routing_scheme from peppol_eas) |  |
| `stock_move` | 3,640 | `scrap_id` | many2one → stock.scrap | 0 | stock | TRANSFORM -> is_scrap boolean |  |

### B.1 Legacy v13 id columns (0) → `etl_idmap.legacy13_id`




### B.2 Dropped columns with no data on 18 (39) → dropped

`stock_move.location_final_id`, `res_partner.company_registry`, `res_partner.company_name`, `res_partner.peppol_eas`, `res_partner.online_partner_information`, `ir_act_server.mail_post_method`, `ir_act_server.sms_method`, `project_task_type.color`, `l10n_in_pan_entity.tds_certificate_filename`, `account_move.l10n_in_withholding_ref_move_id`, `account_move.l10n_in_withholding_ref_payment_id`, `account_move.l10n_in_is_withholding`, `account_move.asset_id`, `account_move.extract_partner_name`, `project_task.email_cc`, `product_template.property_price_difference_account_id`, `product_product.is_in_selected_section_of_order`, `res_users_settings.push_to_talk_key`, `resource_calendar_attendance.week_type`, `resource_calendar_attendance.display_type`, `hr_version.departure_reason_id`, `hr_version.contract_type_id`, `hr_version.ssnid`, `hr_version.departure_date`, `hr_version.departure_description`, `hr_version.additional_note`, `account_tax_group.advance_tax_payment_account_id`, `hr_employee.study_school`, `hr_employee.private_car_plate`, `website_page.date_publish`, `stock_picking_type.auto_show_reception_report`, `mail_activity_type.triggered_next_type_id`, `mail_activity.recommended_activity_type_id`, `project_project_stage.color`, `iap_account.balance`, `resource_calendar.duration_based`, `res_company.predict_bill_product`, `res_company.account_stock_journal_id`, `res_company.account_stock_valuation_id`


## C. Selection keys in use on 18 that no longer exist on 20

| model | field | key on 18 | rows | keys on 20 |
|---|---|---|---:|---|
| account.tax | `l10n_in_tax_type` | `tds_purchase` | 129 | exempt, gst, nil_rated, non_gst, tcs, tds |
| account.tax | `l10n_in_tax_type` | `tds_sale` | 129 | exempt, gst, nil_rated, non_gst, tcs, tds |
| product.template | `tracking` | `none` | 19 | lot, serial |
| resource.calendar.attendance | `day_period` | `lunch` | 5 | afternoon, full_day, morning |

## D. Column type changes

| table | column | 18 | 20 |
|---|---|---|---|
| `ir_model_fields` | `index` | boolean | character varying |
| `account_move_line` | `name` | character varying | text |
| `account_report_expression` | `formula` | character varying | text |
| `account_report_expression` | `subformula` | character varying | text |
| `ir_act_client` | `params_store` | bytea | text |
| `res_groups_privilege` | `placeholder` | character varying | jsonb |
| `res_groups_privilege` | `description` | text | jsonb |
| `website_page` | `url` | character varying | jsonb |
| `iap_service` | `name` | character varying | jsonb |
| `product_category` | `name` | character varying | jsonb |
| `resource_calendar` | `hours_per_week` | double precision | numeric |
| `res_company` | `horizon_days` | double precision | integer |

## E. New NOT NULL columns on 20 without a database default (the mapping must supply a value)

| table | column | type |
|---|---|---|
| `stock_move_line` | `uom_id` | integer |
| `stock_move` | `uom_id` | integer |
| `res_device_log` | `user_agent` | character varying |
| `stock_picking` | `sudi_settlement_mode` | character varying |
| `res_country` | `zip_applicability` | character varying |
| `payment_method` | `provider_id` | integer |
| `account_move` | `review_state` | character varying |
| `account_return_check_template` | `cycle_id` | integer |
| `product_template` | `base_unit_count` | numeric |
| `product_product` | `base_unit_count` | numeric |
| `hr_version` | `tz` | character varying |
| `account_reconcile_model` | `payment_tolerance_type` | character varying |
| `account_reconcile_model` | `matching_order` | character varying |
| `account_reconcile_model` | `payment_tolerance` | double precision |
| `website` | `header_search_type` | character varying |
| `website` | `header_search_order_by` | character varying |
| `account_cash_rounding` | `sequence` | integer |
| `resource_calendar` | `calendar_type` | character varying |
| `res_company` | `picking_policy` | character varying |
| `res_company` | `deferred_expense_periodicity` | character varying |
| `res_company` | `deferred_revenue_periodicity` | character varying |

## F. Natural-key catalogue (reference data matched by key, not by id)

`n18` distinct keys on 18, `n20` on the fresh 20 gate DB, `matched` = 18 keys already present on 20; the rest are created by the ETL.

| model (key) | n18 | n20 | matched | unmatched examples |
|---|---:|---:|---:|---|
| res.company | 1 | 1 | 1 |  |
| res.currency (active) | 2 | 2 | 2 |  |
| res.country | 251 | 251 | 251 |  |
| res.country.state | 2083 | 2132 | 1899 | EG-HU, EG-SU, IN-DD, PK-AJK, PK-BA, PK-GB, PK-IS/ICT, PK-KP/KPK |
| res.lang (active) | 1 | 1 | 0 | en_IN |
| uom.uom | 30 | 30 | 29 | Ton |
| account.account (code) | 110 | 8 | 7 | 100204, 100310, 100400, 100410, 100510, 100520, 100530, 100531 |
| account.tax (name|type|amount) | 424 | 275 | 143 | 0.1% TDS 194O P|none|-0.1000, 0.1% TDS 194O S|none|-0.1000, 0.1% TDS 194Q P|none|-0.1000, 0.1% TDS 194Q S|none|-0.1000, 0.1% TDS 393(1) SI.8(ii) GOODS P|none|-0.1000, 0.1% TDS 393(1) SI.8(ii) GOODS S|none|-0.1000, 0.1% TDS 393(1) SI.8(v) ECOMM P|none|-0.1000, 0.1% TDS 393(1) SI.8(v) ECOMM S|none|-0.1000 |
| account.journal (code) | 8 | 9 | 8 |  |
| account.payment.term | 10 | 10 | 10 |  |
| account.fiscal.position | 8 | 8 | 8 |  |
| account.analytic.plan | 1 | 1 | 0 | Project |
| account.analytic.account | n/a | n/a | n/a |  |
| account.incoterms | 11 | 11 | 11 |  |
| product.category (complete_name) | 3 | n/a | n/a |  |
| product.pricelist | 0 | 0 | 0 |  |
| stock.warehouse (code) | 1 | 1 | 0 | HO |
| stock.location (complete_name) | 12 | 12 | 6 | HO, HO/Input, HO/Output, HO/Packing Zone, HO/Quality Control, HO/Stock |
| stock.picking.type (wh|seq) | 8 | n/a | n/a |  |
| crm.team | 3 | 3 | 3 |  |
| crm.stage | n/a | n/a | n/a |  |
| crm.tag | 0 | 0 | 0 |  |
| crm.lost.reason | n/a | n/a | n/a |  |
| utm.source | 10 | 14 | 8 | Lead Recall, Search engine |
| utm.medium | 10 | 6 | 4 | Banner, Facebook, Google Adwords, LinkedIn, Television, X |
| utm.campaign | 0 | 0 | 0 |  |
| res.partner.category | 0 | 0 | 0 |  |
| res.partner.industry | 23 | 23 | 23 |  |
| mail.message.subtype (xmlid) | 31 | 34 | 31 |  |
| mail.activity.type (xmlid) | 7 | 6 | 6 | hr_skills.mail_activity_data_upload_certification |
| mail.activity.type (name) | 7 | 6 | 6 | Certifications |
| res.groups (xmlid) | 74 | 81 | 73 | stock.group_reception_report |
| ir.model (model) | 667 | 711 | 637 | account.ec.sales.report.handler, account.group, barcodes.barcode_events_mixin, hr.contract.type, hr.departure.wizard, hr.employee.certification.report, hr.timesheet.stop.timer.confirmation.wizard, hr.version.wizard |
| ir.model.fields (model.name, stored) | 8407 | 8974 | 7902 | account.account.asset_model_ids, account.account.create_asset, account.account.l10n_in_tds_tcs_section_id, account.account.multiple_assets_per_line, account.account.website_message_ids, account.analytic.account.website_message_ids, account.asset.account_depreciation_expense_id, account.asset.account_depreciation_id |
| ir.sequence (code) | 7 | 9 | 7 |  |
| ir.cron (xmlid) | 55 | 64 | 52 | base_vat.vies_iap_check_update, payment.cron_post_process_payment_tx, stock_account.ir_cron_post_stock_valuation |
| mail.template (xmlid) | 26 | 33 | 23 | account_followup.demo_followup_email_template_2, account_followup.email_template_followup_1, base_install_request.mail_template_base_install_request |
| ir.actions.report (xmlid) | 48 | 37 | 30 | product.report_product_packaging, product.report_product_template_label_2x7, product.report_product_template_label_4x12, product.report_product_template_label_4x12_noprice, product.report_product_template_label_4x7, product.report_product_template_label_dymo, stock.action_report_location_barcode, stock.action_report_lot_label |
| res.users (login) | 31 | 1 | 1 | +918108985187, +918767886465, +919322335689, +919510518233, +919638243361, +919819998355, +919820069794, +919820084222 |
| hr.department | 1 | 1 | 1 |  |
| hr.job | 0 | 0 | 0 |  |
| helpdesk.team | n/a | n/a | n/a |  |
| helpdesk.stage | n/a | n/a | n/a |  |
| project.project | n/a | n/a | n/a |  |
| project.task.type | 8 | 10 | 8 |  |
| res.bank | 1 | n/a | n/a |  |
| documents.folder/document type | n/a | n/a | n/a |  |

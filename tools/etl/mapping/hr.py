"""Phase 'hr': departments, the 12 employees (v20 creates their hr.version rows itself),
projects/tasks (small), and the 13 timesheet lines."""
import logging

from ..core import OrmStep, TableCopy, IDM
from .. import config

_logger = logging.getLogger("etl.hr")
S = config.SRC_SCHEMA

DEPARTMENT = OrmStep("hr.department", key=["name"], update_existing=False,
                     skip={"complete_name", "parent_path", "total_employee", "expected_employee", "plans_count",
                           "has_read_access"})
EMPLOYEE = OrmStep(
    "hr.employee", key=["name"],
    skip={"version_ids", "current_version_id", "resource_id", "resource_calendar_id", "user_partner_id",
          "address_home_id", "avatar_128", "avatar_256", "avatar_512", "avatar_1024", "avatar_1920",
          "image_128", "image_256", "image_512", "image_1024", "contract_ids", "contracts_count",
          "hr_icon_display", "newly_hired", "departure_reason_id", "departure_description", "departure_date",
          "contract_type_id", "ssnid", "study_school", "private_car_plate", "employee_type",
          "is_flexible", "is_fully_flexible", "distance_home_work", "distance_home_work_unit",
          "hourly_cost", "km_home_work"},
)
PROJECT = OrmStep("project.project", key=["name"], update_existing=False,
                  skip={"analytic_account_id", "account_id", "task_ids", "tasks", "task_count", "open_task_count",
                        "closed_task_count", "milestone_ids", "stage_id", "alias_id", "alias_name", "alias_domain_id",
                        "rating_ids", "collaborator_ids", "last_update_id", "last_update_status", "update_ids",
                        "total_timesheet_time", "encode_uom_in_days", "is_project_overtime", "remaining_hours",
                        "allocated_hours", "effective_hours"})
TASK = OrmStep("project.task", batch=200,
               skip={"email_cc", "stage_id", "personal_stage_type_id", "personal_stage_id", "personal_stage_type_ids",
                     "analytic_account_id", "rating_ids", "attachment_ids", "displayed_image_id",
                     "depend_on_ids", "dependent_ids", "child_ids", "subtask_count", "closed_subtask_count",
                     "recurrence_id", "recurring_task", "timesheet_ids", "total_hours_spent", "effective_hours",
                     "remaining_hours", "subtask_effective_hours", "overtime", "progress", "working_days_open",
                     "working_hours_open", "working_days_close", "working_hours_close"},
               transform=lambda v, r, c: v if v.get("project_id") or not r.get("project_id") else None)
TIMESHEET = OrmStep("account.analytic.line", batch=500,
                    skip={"user_can_validate", "is_timesheet", "timer_start", "timer_pause", "display_timer",
                          "l10n_in_hsn_code"},
                    transform=lambda v, r, c: v)

# timesheet timers still running on prod (the line's timer_* fields are computed from these)
TIMERS = TableCopy(
    "timer.timer", "timer_timer",
    joins=["JOIN etl_idmap m ON m.model = s.res_model AND m.old_id = s.res_id", IDM("tu", "res.users", "s.user_id")],
    where="tu.new_id IS NOT NULL",
    overrides={"id": "s.id + {OFF}", "res_id": "m.new_id", "user_id": "tu.new_id"}, on_conflict="DO NOTHING",
)

STEPS = [DEPARTMENT, EMPLOYEE, PROJECT, TASK, TIMESHEET, TIMERS]

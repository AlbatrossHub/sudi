/** @odoo-module **/

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { useSetupAction } from "@web/search/action_hook";
import { browser } from "@web/core/browser/browser";
import { _t } from "@web/core/l10n/translation";

const SERVICE = "sudi.diamond.billing.review";
// Filters survive a drilldown to an invoice and back (see the analytics
// dashboard for the reasoning): props.state on breadcrumb return, storage otherwise.
const FILTER_KEY = "sudi_diamond_billing.filters";

function readStoredFilters() {
    try {
        return JSON.parse(browser.sessionStorage.getItem(FILTER_KEY)) || {};
    } catch {
        return {};
    }
}

function writeStoredFilters(filters) {
    try {
        browser.sessionStorage.setItem(FILTER_KEY, JSON.stringify(filters));
    } catch {
        // Storage may be unavailable; the screen then opens on its defaults.
    }
}

export class SudiBillingReview extends Component {
    static template = "sudi_diamond_billing.BillingReview";
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");
        this.notification = useService("notification");

        const restored = { ...readStoredFilters(), ...(this.props.state || {}) };
        this.state = useState({
            tab: restored.tab || "ready",
            status: restored.status || "due",
            groupBy: restored.groupBy || "customer",
            partnerId: restored.partnerId || "",
            search: restored.search || "",
            dateFrom: restored.dateFrom || "",
            dateTo: restored.dateTo || "",
            loading: true,
            data: null,
            history: null,
            selected: {},
            expanded: {},
            lines: {},
            dialog: null, // { date, preview, busy }
        });

        this.statusChips = [
            { key: "due", label: _t("Due") },
            { key: "closed", label: _t("Closed") },
            { key: "all", label: _t("All") },
        ];
        this.groupChips = [
            { key: "customer", label: _t("Customer") },
            { key: "month", label: _t("Month") },
            { key: "none", label: _t("None") },
        ];

        useSetupAction({
            getLocalState: () => this.filters(),
        });

        onWillStart(() => this.load());
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------
    filters() {
        return {
            tab: this.state.tab,
            status: this.state.status,
            groupBy: this.state.groupBy,
            partnerId: this.state.partnerId,
            search: this.state.search,
            dateFrom: this.state.dateFrom,
            dateTo: this.state.dateTo,
        };
    }

    async load() {
        this.state.loading = true;
        try {
            this.state.data = await this.orm.call(SERVICE, "get_review_data", [], {
                partner_id: this.state.partnerId ? Number(this.state.partnerId) : null,
                date_from: this.state.dateFrom || null,
                date_to: this.state.dateTo || null,
                status: this.state.status,
                search: this.state.search || null,
                group_by: this.state.groupBy,
            });
            writeStoredFilters(this.filters());
            // Selection only ever holds rows that are still open.
            const openIds = new Set(this.openRows().map((row) => row.id));
            for (const id of Object.keys(this.state.selected)) {
                if (!openIds.has(Number(id))) {
                    delete this.state.selected[id];
                }
            }
            // Expanded rows are refreshed so edited rates and totals stay in step.
            await Promise.all(
                Object.keys(this.state.expanded)
                    .filter((id) => this.state.expanded[id])
                    .map((id) => this.loadLines(Number(id)))
            );
            if (this.state.tab === "history") {
                await this.loadHistory();
            }
        } finally {
            this.state.loading = false;
        }
    }

    async loadLines(pickingId) {
        this.state.lines[pickingId] = await this.orm.call(SERVICE, "get_delivery_lines", [pickingId]);
    }

    async loadHistory() {
        this.state.history = await this.orm.call(SERVICE, "get_history", [], {
            partner_id: this.state.partnerId ? Number(this.state.partnerId) : null,
        });
    }

    get allRows() {
        if (!this.state.data) {
            return [];
        }
        return this.state.data.groups.flatMap((group) => group.rows);
    }

    openRows() {
        return this.allRows.filter((row) => row.open);
    }

    get selectedRows() {
        return this.allRows.filter((row) => this.state.selected[row.id]);
    }

    get selection() {
        const rows = this.selectedRows;
        const invoiceCustomers = new Set();
        const referenceCustomers = new Set();
        let pcs = 0;
        let carats = 0;
        let amount = 0;
        for (const row of rows) {
            pcs += row.pcs;
            carats += row.carats;
            amount += row.amount;
            (row.mode === "reference" ? referenceCustomers : invoiceCustomers).add(row.customer_id);
        }
        const parts = [];
        if (invoiceCustomers.size) {
            parts.push(
                invoiceCustomers.size === 1
                    ? _t("1 draft invoice")
                    : _t("%s draft invoices", invoiceCustomers.size)
            );
        }
        if (referenceCustomers.size) {
            parts.push(
                referenceCustomers.size === 1
                    ? _t("1 reference statement")
                    : _t("%s reference statements", referenceCustomers.size)
            );
        }
        return { count: rows.length, pcs, carats, amount, split: parts.join(" + "), parts };
    }

    // ------------------------------------------------------------------
    // Formatting
    // ------------------------------------------------------------------
    money(value) {
        const currency = (this.state.data && this.state.data.currency) || "INR";
        try {
            return new Intl.NumberFormat("en-IN", {
                style: "currency",
                currency,
                maximumFractionDigits: 0,
            }).format(value || 0);
        } catch {
            return String(Math.round(value || 0));
        }
    }

    pcs(value) {
        return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 }).format(value || 0);
    }

    carats(value) {
        return new Intl.NumberFormat("en-IN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }).format(value || 0);
    }

    date(iso) {
        if (!iso) {
            return "";
        }
        const [year, month, day] = iso.split("-");
        const months = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
        return `${day} ${months[Number(month) - 1]} ${year}`;
    }

    pillClass(status) {
        return {
            to_bill: "o_sb_pill o_sb_p_due",
            partial: "o_sb_pill o_sb_p_part",
            billed: "o_sb_pill o_sb_p_billed",
            closed: "o_sb_pill o_sb_p_closed",
            no_charge: "o_sb_pill o_sb_p_nc",
        }[status] || "o_sb_pill";
    }

    // ------------------------------------------------------------------
    // Filters
    // ------------------------------------------------------------------
    setTab(tab) {
        this.state.tab = tab;
        writeStoredFilters(this.filters());
        if (tab === "history" && !this.state.history) {
            this.loadHistory();
        }
    }

    setStatus(status) {
        if (status !== this.state.status) {
            this.state.status = status;
            this.load();
        }
    }

    setGroupBy(groupBy) {
        if (groupBy !== this.state.groupBy) {
            this.state.groupBy = groupBy;
            this.load();
        }
    }

    onPartnerChange(ev) {
        this.state.partnerId = ev.target.value;
        this.state.history = null;
        this.load();
    }

    onSearch(ev) {
        this.state.search = ev.target.value.trim();
        this.load();
    }

    onDateChange(which, ev) {
        this.state[which] = ev.target.value;
        this.load();
    }

    // ------------------------------------------------------------------
    // Rows
    // ------------------------------------------------------------------
    isGroupAllSelected(group) {
        const due = group.rows.filter((row) => row.open);
        return due.length > 0 && due.every((row) => this.state.selected[row.id]);
    }

    toggleGroup(group) {
        const all = this.isGroupAllSelected(group);
        for (const row of group.rows) {
            if (!row.open) {
                continue;
            }
            if (all) {
                delete this.state.selected[row.id];
            } else {
                this.state.selected[row.id] = true;
            }
        }
    }

    toggleRow(row) {
        if (this.state.selected[row.id]) {
            delete this.state.selected[row.id];
        } else {
            this.state.selected[row.id] = true;
        }
    }

    clearSelection() {
        this.state.selected = {};
    }

    async toggleExpand(row) {
        const next = !this.state.expanded[row.id];
        this.state.expanded[row.id] = next;
        if (next && !this.state.lines[row.id]) {
            await this.loadLines(row.id);
        }
    }

    async setMode(row, mode) {
        if (row.mode === mode) {
            return;
        }
        await this.orm.call(SERVICE, "set_settlement_mode", [[row.id], mode]);
        row.mode = mode;
    }

    async onRateChange(row, line, ev) {
        const value = parseFloat(ev.target.value);
        if (Number.isNaN(value) || value < 0) {
            ev.target.value = line.rate;
            return;
        }
        if (value === line.rate) {
            return;
        }
        await this.orm.call(SERVICE, "set_line_price", [line.move_id, value]);
        await this.load();
    }

    async toggleNoCharge(row, line) {
        await this.orm.call(SERVICE, "set_returned_without_work", [[line.move_id], !line.no_charge]);
        await this.load();
    }

    async resetPrices(rows) {
        await this.orm.call(SERVICE, "reset_prices", [rows.map((row) => row.id)]);
        await this.load();
        this.notification.add(_t("Rates refreshed from the customer price list."), { type: "success" });
    }

    async openRecord(model, resId) {
        const action = await this.orm.call(SERVICE, "get_open_action", [model, resId]);
        browser.history.pushState({}, "", browser.location.href);
        this.actionService.doAction(action);
    }

    // ------------------------------------------------------------------
    // Settlement
    // ------------------------------------------------------------------
    async settleGroup(group) {
        for (const row of group.rows) {
            if (row.open) {
                this.state.selected[row.id] = true;
            }
        }
        await this.openDialog();
    }

    async billAllDue() {
        const ids = await this.orm.call(SERVICE, "get_due_delivery_ids", []);
        if (!ids.length) {
            this.notification.add(_t("No monthly customer is due right now."), { type: "info" });
            return;
        }
        this.state.tab = "ready";
        this.state.status = "due";
        this.state.partnerId = "";
        this.state.search = "";
        this.state.dateFrom = "";
        this.state.dateTo = "";
        await this.load();
        this.state.selected = {};
        for (const id of ids) {
            this.state.selected[id] = true;
        }
        await this.openDialog();
    }

    async openDialog() {
        const ids = this.selectedRows.map((row) => row.id);
        if (!ids.length) {
            return;
        }
        const preview = await this.orm.call(SERVICE, "get_settlement_preview", [ids]);
        this.state.dialog = { date: this.state.data.today, preview, busy: false };
    }

    closeDialog() {
        this.state.dialog = null;
    }

    onDialogDate(ev) {
        this.state.dialog.date = ev.target.value;
    }

    get confirmLabel() {
        const parts = this.selection.parts;
        return parts.length ? _t("Create %s", parts.join(" & ")) : _t("Create documents");
    }

    async confirmSettlement() {
        const dialog = this.state.dialog;
        if (!dialog || dialog.busy) {
            return;
        }
        dialog.busy = true;
        const ids = this.selectedRows.map((row) => row.id);
        let result;
        try {
            result = await this.orm.call(SERVICE, "settle", [ids], { date: dialog.date || null });
        } finally {
            dialog.busy = false;
        }
        this.state.dialog = null;
        this.state.selected = {};
        this.state.history = null;
        const parts = [];
        if (result.invoices.length) {
            parts.push(
                result.invoices.length === 1
                    ? _t("1 draft invoice")
                    : _t("%s draft invoices", result.invoices.length)
            );
        }
        if (result.statement_count) {
            parts.push(
                result.statement_count === 1
                    ? _t("1 reference statement")
                    : _t("%s reference statements", result.statement_count)
            );
        }
        this.notification.add(_t("Created %s. Deliveries marked and logged.", parts.join(" and ")), {
            type: "success",
        });
        await this.load();
        if (result.action) {
            browser.history.pushState({}, "", browser.location.href);
            this.actionService.doAction(result.action);
        }
    }
}

registry.category("actions").add("sudi_diamond_billing_review", SudiBillingReview);

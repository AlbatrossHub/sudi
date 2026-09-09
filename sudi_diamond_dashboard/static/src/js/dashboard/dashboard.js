import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { useSetupAction } from "@web/search/action_hook";
import { browser } from "@web/core/browser/browser";

// Filters survive a drilldown two ways: useSetupAction() hands them back as
// props.state when returning through the breadcrumb, and sessionStorage keeps
// them if that state is missing (a hard reload, or a fresh action instance).
const FILTER_KEY = "sudi_diamond_dashboard.filters";

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
        // Storage can be unavailable (private windows, blocked site data);
        // the dashboard then simply opens on its defaults.
    }
}
import { _t } from "@web/core/l10n/translation";
import { formatFloat, formatInteger } from "@web/views/fields/formatters";
import { SudiTrendChart } from "./trend_chart";

export class SudiDiamondDashboard extends Component {
    static template = "sudi_diamond_dashboard.SudiDashboard";
    static components = { SudiTrendChart };
    static props = { ...standardActionServiceProps };

    setup() {
        this.orm = useService("orm");
        this.actionService = useService("action");

        this.periods = [
            { key: "day", label: _t("Day") },
            { key: "week", label: _t("Week") },
            { key: "month", label: _t("Month") },
            { key: "year", label: _t("Year") },
            { key: "custom", label: _t("Custom") },
        ];
        this.measures = [
            { key: "pcs", label: _t("Pieces") },
            { key: "carats", label: _t("Carats") },
        ];

        // Restored from props.state when the user comes back through the
        // breadcrumb after a drilldown, so returning does not silently reset the
        // period, measure or custom range they had chosen. Figures are
        // deliberately not persisted -- they are refetched so the numbers are
        // never stale.
        // props.state wins when present -- it is the state of the exact view the
        // user navigated away from; storage is the fallback.
        const restored = { ...readStoredFilters(), ...(this.props.state || {}) };
        this.state = useState({
            period: restored.period || "month",
            measure: restored.measure || "pcs",
            dateFrom: restored.dateFrom || "",
            dateTo: restored.dateTo || "",
            loading: true,
            data: null,
        });

        useSetupAction({
            getLocalState: () => ({
                period: this.state.period,
                measure: this.state.measure,
                dateFrom: this.state.dateFrom,
                dateTo: this.state.dateTo,
            }),
        });

        // Passed to the chart, which formats tooltips with the active measure.
        this.formatValue = this.formatValue.bind(this);

        onWillStart(() => this.load());
    }

    // ------------------------------------------------------------------
    // Data
    // ------------------------------------------------------------------
    async load() {
        this.state.loading = true;
        const isCustom = this.state.period === "custom";
        try {
            const data = await this.orm.call(
                "sudi.diamond.dashboard",
                "get_dashboard_data",
                [],
                {
                    period: this.state.period,
                    measure: this.state.measure,
                    date_from: isCustom ? this.state.dateFrom : null,
                    date_to: isCustom ? this.state.dateTo : null,
                }
            );
            this.state.data = data;
            this.state.dateFrom = data.date_from;
            this.state.dateTo = data.date_to;
            writeStoredFilters({
                period: this.state.period,
                measure: this.state.measure,
                dateFrom: this.state.dateFrom,
                dateTo: this.state.dateTo,
            });
        } finally {
            this.state.loading = false;
        }
    }

    onSelectPeriod(period) {
        if (period === this.state.period) {
            return;
        }
        this.state.period = period;
        // Entering Custom starts from whatever window is on screen.
        this.load();
    }

    onSelectMeasure(measure) {
        if (measure === this.state.measure) {
            return;
        }
        this.state.measure = measure;
        this.load();
    }

    onDateChange(which, ev) {
        const value = ev.target.value;
        if (!value) {
            return;
        }
        this.state[which] = value;
        if (this.state.dateFrom && this.state.dateTo) {
            this.load();
        }
    }

    onRowKeydown(ev, kind, recordId) {
        if (ev.key === "Enter" || ev.key === " ") {
            ev.preventDefault();
            this.openDrilldown(kind, recordId);
        }
    }

    async openDrilldown(kind, recordId) {
        const action = await this.orm.call(
            "sudi.diamond.dashboard",
            "get_drilldown_action",
            [kind, recordId || false],
            {
                period: this.state.period,
                date_from: this.state.dateFrom,
                date_to: this.state.dateTo,
            }
        );
        // Odoo navigates with router.pushState(..., { replace: true }), which
        // overwrites this dashboard's history entry -- the browser Back button
        // would then skip it and land on the apps home. Duplicating the current
        // entry first gives Odoo a copy to overwrite, so the original survives and
        // Back returns here. Filters come back from sessionStorage, since a
        // history navigation remounts the action without props.state.
        browser.history.pushState({}, "", browser.location.href);
        this.actionService.doAction(action);
    }

    // ------------------------------------------------------------------
    // Formatting helpers used by the template
    // ------------------------------------------------------------------
    formatValue(value) {
        if (this.state.measure === "carats") {
            return formatFloat(value, { digits: [16, 2] });
        }
        return formatInteger(Math.round(value || 0));
    }

    formatCount(value) {
        return formatInteger(value || 0);
    }

    percent(share) {
        return `${Math.round((share || 0) * 100)}%`;
    }

    /** Bars are scaled against the leader, so the top row always fills the track. */
    barWidth(entry, rows) {
        const max = rows.length ? rows[0].value : 0;
        return max ? `${((entry.value / max) * 100).toFixed(1)}%` : "0%";
    }

    stageWidth(stage) {
        return `${((stage.share || 0) * 100).toFixed(1)}%`;
    }

    deltaClass(value) {
        return value >= 0 ? "o_sudi_delta o_sudi_delta_up" : "o_sudi_delta o_sudi_delta_down";
    }

    deltaText(value) {
        const arrow = value >= 0 ? "▲" : "▼";
        return `${arrow} ${Math.abs(value).toFixed(1)}%`;
    }

    get qtyLabel() {
        return _t("Total %s", this.state.data.measure_label);
    }

    get avgLabel() {
        return _t("Avg %s / Receipt", this.state.data.measure_label);
    }

    get trendTitle() {
        return _t("%s processed", this.state.data.measure_label);
    }

    get rankNote() {
        return _t("by %s", this.state.data.measure_label.toLowerCase());
    }
}

registry.category("actions").add("sudi_diamond_dashboard", SudiDiamondDashboard);

import { Component, onWillStart, useState } from "@odoo/owl";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
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

        this.state = useState({
            period: "month",
            measure: "pcs",
            dateFrom: "",
            dateTo: "",
            loading: true,
            data: null,
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

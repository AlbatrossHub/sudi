import { Component, onWillStart, onWillUnmount, useEffect, useRef } from "@odoo/owl";
import { loadBundle } from "@web/core/assets";
import { humanNumber } from "@web/core/utils/numbers";

/**
 * Quantity trend for the analytical dashboard.
 *
 * Every colour is set per-chart rather than on ``Chart.defaults``: the library
 * is shared with Odoo's own graph views, and its default tooltip is black,
 * which this design does not use anywhere.
 */
export class SudiTrendChart extends Component {
    static template = "sudi_diamond_dashboard.SudiTrendChart";
    static props = {
        labels: { type: Array },
        values: { type: Array },
        unit: { type: String },
        formatValue: { type: Function },
    };

    setup() {
        this.canvasRef = useRef("canvas");
        this.chart = null;
        onWillStart(() => loadBundle("web.chartjs_lib"));
        useEffect(() => this.renderChart());
        onWillUnmount(() => this.destroyChart());
    }

    destroyChart() {
        if (this.chart) {
            this.chart.destroy();
            this.chart = null;
        }
    }

    /** Soft dashed rule under the hovered point. */
    get crosshairPlugin() {
        return {
            id: "sudiCrosshair",
            afterDatasetsDraw: (chart) => {
                const active = chart.tooltip?.getActiveElements?.() || [];
                if (!active.length) {
                    return;
                }
                const { ctx, chartArea } = chart;
                ctx.save();
                ctx.strokeStyle = "#c4cddd";
                ctx.lineWidth = 1;
                ctx.setLineDash([3, 4]);
                ctx.beginPath();
                ctx.moveTo(active[0].element.x, chartArea.top);
                ctx.lineTo(active[0].element.x, chartArea.bottom);
                ctx.stroke();
                ctx.restore();
            },
        };
    }

    renderChart() {
        const canvas = this.canvasRef.el;
        if (!canvas || typeof Chart === "undefined") {
            return;
        }
        this.destroyChart();

        const ctx = canvas.getContext("2d");
        const gradient = ctx.createLinearGradient(0, 0, 0, 250);
        gradient.addColorStop(0, "rgba(242, 96, 26, 0.30)");
        gradient.addColorStop(1, "rgba(242, 96, 26, 0.02)");

        const lastIndex = this.props.values.length - 1;
        const formatValue = this.props.formatValue;
        const unit = this.props.unit;
        const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)").matches;

        this.chart = new Chart(ctx, {
            type: "line",
            data: {
                labels: this.props.labels,
                datasets: [
                    {
                        data: this.props.values,
                        borderColor: "#e2560f",
                        borderWidth: 2,
                        fill: true,
                        backgroundColor: gradient,
                        tension: 0.38,
                        pointRadius: (context) => (context.dataIndex === lastIndex ? 5 : 0),
                        pointHoverRadius: 5,
                        pointBackgroundColor: "#e2560f",
                        pointBorderColor: "#ffffff",
                        pointBorderWidth: 2.5,
                        pointHoverBackgroundColor: "#e2560f",
                        pointHoverBorderColor: "#ffffff",
                        pointHoverBorderWidth: 2.5,
                    },
                ],
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                animation: reduceMotion ? false : { duration: 550 },
                interaction: { mode: "index", intersect: false },
                layout: { padding: { top: 6, right: 6 } },
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: "#ffffff",
                        titleColor: "#37415a",
                        bodyColor: "#5f6b85",
                        borderColor: "#d7dfec",
                        borderWidth: 1,
                        cornerRadius: 12,
                        padding: 12,
                        displayColors: false,
                        titleFont: { family: "Sora, sans-serif", weight: "600", size: 12.5 },
                        bodyFont: { family: "IBM Plex Mono, monospace", size: 12.5 },
                        callbacks: {
                            label: (item) => `${formatValue(item.parsed.y)} ${unit}`,
                        },
                    },
                },
                scales: {
                    x: {
                        grid: { display: false },
                        border: { color: "#d7dfec" },
                        ticks: {
                            color: "#8e99b0",
                            maxRotation: 0,
                            autoSkipPadding: 12,
                            font: { family: "Manrope, sans-serif", size: 11.5 },
                        },
                    },
                    y: {
                        beginAtZero: true,
                        grid: { color: "#dde4ef", drawTicks: false },
                        border: { display: false, dash: [3, 4] },
                        ticks: {
                            color: "#8e99b0",
                            padding: 8,
                            maxTicksLimit: 5,
                            font: { family: "Manrope, sans-serif", size: 11.5 },
                            callback: (value) => humanNumber(value, { decimals: 1 }),
                        },
                    },
                },
            },
            plugins: [this.crosshairPlugin],
        });
    }
}

/** @odoo-module **/

import { registry } from "@web/core/registry";
import { ImageField, imageField } from "@web/views/fields/image/image_field";
import { useState, useRef, onMounted, onWillUnmount } from "@odoo/owl";
import { closestScrollableY } from "@web/core/utils/scrolling";

// Once the job table has this many rows the Jangad docks to the side and stays
// there, instead of waiting for it to scroll out of sight. Filling more than a
// couple of rows means constantly reading sizes and carats off the image.
const DOCK_MIN_JOB_ROWS = 2;

export class JangadImageViewerField extends ImageField {
    static template = "diamond.JangadImageViewerField";

    setup() {
        super.setup();
        this.viewerState = useState({
            scale: 1,
            rotation: 0,
            translateX: 0,
            translateY: 0,
            isHovering: false,
            lensX: 0,
            lensY: 0,
            lensBgX: 0,
            lensBgY: 0,
            isDragging: false,
            // Floating viewer (see "Floating viewer" below)
            isFloating: false,
            floatDismissed: false,
            floatTop: 0,
            floatLeft: 0,
            floatWidth: 260,
            isWide: window.innerWidth >= 992,
        });

        this.containerRef = useRef("viewerContainer");
        this.imgRef = useRef("viewerImg");

        // Touch tracking variables
        this.touchStartDist = 0;
        this.touchStartScale = 1;
        this.touchStartX = 0;
        this.touchStartY = 0;
        this.startTranslateX = 0;
        this.startTranslateY = 0;

        // Reposition on scroll/resize, throttled to one frame.
        this.floatFrame = null;
        this.onFloatReposition = () => {
            if (this.floatFrame) {
                return;
            }
            this.floatFrame = window.requestAnimationFrame(() => {
                this.floatFrame = null;
                this.positionFloatingViewer();
            });
        };
        onMounted(() => {
            this.startFloatWatch();
            // Place it now so the first paint is roughly right, then again next
            // frame once the chatter column exists to measure against.
            this.positionFloatingViewer();
            this.onFloatReposition();
        });
        onWillUnmount(() => this.stopFloatWatch());
    }

    // ── Floating viewer ──────────────────────────────────────────────────
    // Filling the job table scrolls the Jangad out of sight. Once the inline
    // viewer leaves the top of the viewport we mirror it into a small panel
    // fixed over the chatter column, so the operator can keep reading sizes
    // and carats off the image while typing rows.

    get hasJangad() {
        return Boolean(this.props.record.data[this.props.name]);
    }

    /** Number of rows currently in the job table, if the view shows it. */
    get jobRowCount() {
        const list = this.props.record.data && this.props.record.data.move_ids;
        if (!list) {
            return 0;
        }
        if (typeof list.count === "number") {
            return list.count;
        }
        return Array.isArray(list.records) ? list.records.length : 0;
    }

    get showFloatingViewer() {
        if (!this.hasJangad || this.viewerState.floatDismissed || !this.viewerState.isWide) {
            return false;
        }
        // Dock permanently once the table is being filled; otherwise only once the
        // inline viewer has actually scrolled away.
        return this.jobRowCount >= DOCK_MIN_JOB_ROWS || this.viewerState.isFloating;
    }

    get floatStyle() {
        const { floatLeft, floatTop, floatWidth } = this.viewerState;
        return `left: ${floatLeft}px; top: ${floatTop}px; width: ${floatWidth}px;`;
    }

    get floatImgStyle() {
        const { scale, rotation } = this.viewerState;
        return `transform: rotate(${rotation}deg) scale(${scale}); transition: transform .15s ease-out;`;
    }

    startFloatWatch() {
        const el = this.containerRef.el;
        if (!el || typeof IntersectionObserver === "undefined") {
            return;
        }
        // The form scrolls inside .o_content, which starts well below the navbar
        // and control panel -- so "off the top" is the scroller's top edge, never 0.
        // closestScrollableY only matches an element that is scrollable *right now*,
        // which a short form is not, so anchor to .o_content directly and keep it as
        // the fallback. Below 992px .o_content stops scrolling, but we disable the
        // panel there anyway.
        const scroller = el.closest(".o_content") || closestScrollableY(el);
        this.floatObserver = new IntersectionObserver(
            ([entry]) => {
                // Only float when the field has scrolled off the TOP. Leaving the
                // bottom means the user is above it and can already see it.
                const rootTop = entry.rootBounds ? entry.rootBounds.top : 0;
                const scrolledPastTop = entry.boundingClientRect.bottom <= rootTop + 1;
                const wide = window.innerWidth >= 992;
                this.viewerState.isFloating = !entry.isIntersecting && scrolledPastTop && wide;
                if (this.viewerState.isFloating) {
                    this.positionFloatingViewer();
                } else {
                    // Scrolling back to the field re-arms a panel the user dismissed.
                    this.viewerState.floatDismissed = false;
                }
            },
            { root: scroller || null, threshold: 0 }
        );
        this.floatObserver.observe(el);
        // Capture phase: the form scrolls inside .o_content, not on window.
        window.addEventListener("scroll", this.onFloatReposition, true);
        window.addEventListener("resize", this.onFloatReposition);
    }

    stopFloatWatch() {
        if (this.floatObserver) {
            this.floatObserver.disconnect();
            this.floatObserver = null;
        }
        if (this.floatFrame) {
            window.cancelAnimationFrame(this.floatFrame);
            this.floatFrame = null;
        }
        window.removeEventListener("scroll", this.onFloatReposition, true);
        window.removeEventListener("resize", this.onFloatReposition);
    }

    /** Sit over the chatter when it is aside, otherwise dock to the right edge. */
    positionFloatingViewer() {
        this.viewerState.isWide = window.innerWidth >= 992;
        const margin = 12;
        const chatter = document.querySelector(".o-mail-Chatter");
        const rect = chatter && chatter.getBoundingClientRect();
        // A chatter on the right half is the aside layout; below the form it is not.
        const isAside = rect && rect.width > 200 && rect.height > 0 && rect.left > window.innerWidth / 2;

        if (isAside) {
            this.viewerState.floatWidth = Math.round(rect.width - margin * 2);
            this.viewerState.floatLeft = Math.round(rect.left + margin);
            this.viewerState.floatTop = Math.round(rect.top + margin);
        } else {
            const width = Math.min(280, Math.round(window.innerWidth * 0.3));
            this.viewerState.floatWidth = width;
            this.viewerState.floatLeft = window.innerWidth - width - margin;
            this.viewerState.floatTop = 100;
        }
    }

    dismissFloatingViewer() {
        this.viewerState.floatDismissed = true;
    }

    scrollToInlineViewer() {
        if (this.containerRef.el) {
            this.containerRef.el.scrollIntoView({ behavior: "smooth", block: "center" });
        }
    }

    get transformStyle() {
        const { scale, rotation, translateX, translateY, isDragging } = this.viewerState;
        const transition = isDragging ? "none" : "transform 0.15s ease-out";
        return `transform: translate(${translateX}px, ${translateY}px) rotate(${rotation}deg) scale(${scale}); transition: ${transition};`;
    }

    // Controls
    zoomIn() {
        this.viewerState.scale = Math.min(5, +(this.viewerState.scale + 0.25).toFixed(2));
    }

    zoomOut() {
        this.viewerState.scale = Math.max(0.5, +(this.viewerState.scale - 0.25).toFixed(2));
    }

    rotateClockwise() {
        this.viewerState.rotation = (this.viewerState.rotation + 90) % 360;
    }

    rotateCounterClockwise() {
        this.viewerState.rotation = (this.viewerState.rotation - 90 + 360) % 360;
    }

    resetView() {
        this.viewerState.scale = 1;
        this.viewerState.rotation = 0;
        this.viewerState.translateX = 0;
        this.viewerState.translateY = 0;
        this.viewerState.isHovering = false;
    }

    // Hover Zoom Lens (Desktop)
    onMouseEnter() {
        if (this.viewerState.scale === 1 && this.viewerState.translateX === 0 && this.viewerState.translateY === 0) {
            this.viewerState.isHovering = true;
        }
    }

    onMouseLeave() {
        this.viewerState.isHovering = false;
    }

    onMouseMove(ev) {
        if (!this.viewerState.isHovering || !this.containerRef.el) {
            return;
        }
        if (this.viewerState.scale !== 1 || this.viewerState.translateX !== 0 || this.viewerState.translateY !== 0) {
            this.viewerState.isHovering = false;
            return;
        }

        const rect = this.containerRef.el.getBoundingClientRect();
        const mouseX = ev.clientX - rect.left;
        const mouseY = ev.clientY - rect.top;

        const lensSize = 120;
        const halfLens = lensSize / 2;

        let lensX = mouseX - halfLens;
        let lensY = mouseY - halfLens;

        lensX = Math.max(0, Math.min(rect.width - lensSize, lensX));
        lensY = Math.max(0, Math.min(rect.height - lensSize, lensY));

        const zoomLevel = 2.5;
        const bgX = -((mouseX / rect.width) * (rect.width * zoomLevel - lensSize));
        const bgY = -((mouseY / rect.height) * (rect.height * zoomLevel - lensSize));

        this.viewerState.lensX = lensX;
        this.viewerState.lensY = lensY;
        this.viewerState.lensBgX = bgX;
        this.viewerState.lensBgY = bgY;
    }

    // Drag / Pan (Desktop Mouse)
    onMouseDown(ev) {
        if (ev.button !== 0) return;
        if (this.viewerState.scale <= 1 && this.viewerState.rotation === 0) return;

        this.viewerState.isDragging = true;
        this.touchStartX = ev.clientX;
        this.touchStartY = ev.clientY;
        this.startTranslateX = this.viewerState.translateX;
        this.startTranslateY = this.viewerState.translateY;

        const onWindowMouseMove = (e) => {
            if (!this.viewerState.isDragging) return;
            const deltaX = e.clientX - this.touchStartX;
            const deltaY = e.clientY - this.touchStartY;
            this.viewerState.translateX = this.startTranslateX + deltaX;
            this.viewerState.translateY = this.startTranslateY + deltaY;
        };

        const onWindowMouseUp = () => {
            this.viewerState.isDragging = false;
            window.removeEventListener("mousemove", onWindowMouseMove);
            window.removeEventListener("mouseup", onWindowMouseUp);
        };

        window.addEventListener("mousemove", onWindowMouseMove);
        window.addEventListener("mouseup", onWindowMouseUp);
    }

    // Touch Gestures (Mobile Pinch-to-Zoom & Drag-to-Pan)
    onTouchStart(ev) {
        if (ev.touches.length === 1) {
            this.viewerState.isDragging = true;
            this.touchStartX = ev.touches[0].clientX;
            this.touchStartY = ev.touches[0].clientY;
            this.startTranslateX = this.viewerState.translateX;
            this.startTranslateY = this.viewerState.translateY;
        } else if (ev.touches.length === 2) {
            this.viewerState.isDragging = true;
            const dx = ev.touches[0].clientX - ev.touches[1].clientX;
            const dy = ev.touches[0].clientY - ev.touches[1].clientY;
            this.touchStartDist = Math.hypot(dx, dy);
            this.touchStartScale = this.viewerState.scale;
        }
    }

    onTouchMove(ev) {
        if (!this.viewerState.isDragging) return;

        if (ev.touches.length === 1) {
            const deltaX = ev.touches[0].clientX - this.touchStartX;
            const deltaY = ev.touches[0].clientY - this.touchStartY;
            this.viewerState.translateX = this.startTranslateX + deltaX;
            this.viewerState.translateY = this.startTranslateY + deltaY;
        } else if (ev.touches.length === 2) {
            const dx = ev.touches[0].clientX - ev.touches[1].clientX;
            const dy = ev.touches[0].clientY - ev.touches[1].clientY;
            const dist = Math.hypot(dx, dy);
            if (this.touchStartDist > 0) {
                const scaleFactor = dist / this.touchStartDist;
                const newScale = Math.max(0.5, Math.min(5, this.touchStartScale * scaleFactor));
                this.viewerState.scale = +newScale.toFixed(2);
            }
        }
    }

    onTouchEnd(ev) {
        if (ev.touches.length === 0) {
            this.viewerState.isDragging = false;
        } else if (ev.touches.length === 1) {
            this.touchStartX = ev.touches[0].clientX;
            this.touchStartY = ev.touches[0].clientY;
            this.startTranslateX = this.viewerState.translateX;
            this.startTranslateY = this.viewerState.translateY;
        }
    }
}

export const jangadImageViewerField = {
    ...imageField,
    component: JangadImageViewerField,
};

registry.category("fields").add("jangad_image_viewer", jangadImageViewerField);

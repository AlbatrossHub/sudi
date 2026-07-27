/** @odoo-module **/

import { registry } from "@web/core/registry";
import { ImageField, imageField } from "@web/views/fields/image/image_field";
import { useState, useRef } from "@odoo/owl";

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

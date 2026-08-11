/** @odoo-module **/

import { Component, useState, onMounted } from "@odoo/owl";
import { registry } from "@web/core/registry";

/**
 * PWA Preview Component
 * Provides a live preview of PWA branding settings
 */
export class PWAPreview extends Component {
      static template = "pwa_dynamic_branding.PWAPreview";
      static props = {
            appName: { type: String, optional: true },
            themeColor: { type: String, optional: true },
            backgroundColor: { type: String, optional: true },
            iconUrl: { type: String, optional: true },
      };

      setup() {
            this.state = useState({
                  appName: this.props.appName || "Odoo",
                  themeColor: this.props.themeColor || "#714B67",
                  backgroundColor: this.props.backgroundColor || "#714B67",
                  iconUrl: this.props.iconUrl || "/web/static/img/odoo-icon-192x192.png",
            });

            onMounted(() => {
                  this.updatePreview();
            });
      }

      updatePreview() {
            // Update CSS custom properties for real-time preview
            const root = document.documentElement;
            root.style.setProperty("--pwa-theme-color", this.state.themeColor);
            root.style.setProperty("--pwa-background-color", this.state.backgroundColor);
      }

      onColorChange(field, event) {
            this.state[field] = event.target.value;
            this.updatePreview();
      }
}

/**
 * PWA Color Picker Widget
 * Enhanced color picker with preview
 */
export class PWAColorPicker extends Component {
      static template = "pwa_dynamic_branding.PWAColorPicker";
      static props = {
            value: { type: String },
            onChange: { type: Function },
            label: { type: String, optional: true },
      };

      setup() {
            this.state = useState({
                  color: this.props.value || "#714B67",
                  showPicker: false,
            });
      }

      onColorInput(event) {
            this.state.color = event.target.value;
            if (this.props.onChange) {
                  this.props.onChange(event.target.value);
            }
      }

      togglePicker() {
            this.state.showPicker = !this.state.showPicker;
      }

      selectPresetColor(color) {
            this.state.color = color;
            if (this.props.onChange) {
                  this.props.onChange(color);
            }
      }

      get presetColors() {
            return [
                  "#714B67", // Odoo Purple
                  "#875A7B", // Odoo Secondary
                  "#00A09D", // Odoo Accent
                  "#2196F3", // Blue
                  "#4CAF50", // Green
                  "#FF9800", // Orange
                  "#E91E63", // Pink
                  "#9C27B0", // Purple
                  "#00BCD4", // Cyan
                  "#FF5722", // Deep Orange
            ];
      }
}

/**
 * PWA Installation Helper
 * Provides UI for PWA installation prompts
 */
export class PWAInstallHelper {
      constructor() {
            this.deferredPrompt = null;
            this.setupInstallPrompt();
      }

      setupInstallPrompt() {
            window.addEventListener("beforeinstallprompt", (event) => {
                  event.preventDefault();
                  this.deferredPrompt = event;
                  this.showInstallButton();
            });

            window.addEventListener("appinstalled", () => {
                  this.deferredPrompt = null;
                  this.hideInstallButton();
                  this.showNotification("App installed successfully!", "success");
            });
      }

      async promptInstall() {
            if (!this.deferredPrompt) {
                  return false;
            }

            this.deferredPrompt.prompt();
            const { outcome } = await this.deferredPrompt.userChoice;
            this.deferredPrompt = null;

            return outcome === "accepted";
      }

      showInstallButton() {
            // Dispatch event for UI components to show install button
            window.dispatchEvent(new CustomEvent("pwa:show-install-button"));
      }

      hideInstallButton() {
            // Dispatch event for UI components to hide install button
            window.dispatchEvent(new CustomEvent("pwa:hide-install-button"));
      }

      showNotification(message, type = "info") {
            // Use Odoo's notification service if available
            try {
                  const notificationService = registry.category("services").get("notification");
                  if (notificationService && notificationService.add) {
                        notificationService.add(message, { type });
                  } else {
                        // Fallback to console if service not available
                        console.log(`[${type.toUpperCase()}] ${message}`);
                  }
            } catch (error) {
                  // Fallback to console on error
                  console.log(`[${type.toUpperCase()}] ${message}`);
            }
      }

      /**
       * Check if the app is running as installed PWA
       */
      isInstalledPWA() {
            return (
                  window.matchMedia("(display-mode: standalone)").matches ||
                  window.navigator.standalone === true
            );
      }

      /**
       * Get current PWA configuration
       */
      async getPWAConfig() {
            try {
                  const response = await fetch("/pwa/config", {
                        method: "POST",
                        headers: {
                              "Content-Type": "application/json",
                        },
                        body: JSON.stringify({}),
                  });
                  const data = await response.json();
                  return data.result;
            } catch (error) {
                  console.error("Failed to fetch PWA config:", error);
                  return null;
            }
      }
}

// Initialize PWA Install Helper globally
if (typeof window !== "undefined") {
      window.pwaInstallHelper = new PWAInstallHelper();
}

/**
 * PWA Settings Preview Service
 * Updates preview in real-time as settings change
 */
export const pwaPreviewService = {
      dependencies: ["notification"],

      start(env, { notification }) {
            return {
                  updatePreview(settings) {
                        // Update preview elements
                        const previewContainer = document.querySelector(".pwa-preview-container");
                        if (previewContainer) {
                              if (settings.backgroundColor) {
                                    previewContainer.style.backgroundColor = settings.backgroundColor;
                              }
                              if (settings.appName) {
                                    const nameEl = previewContainer.querySelector(".pwa-app-name");
                                    if (nameEl) {
                                          nameEl.textContent = settings.appName;
                                    }
                              }
                        }
                  },

                  async refreshManifest() {
                        // Force refresh of the web manifest
                        const link = document.querySelector('link[rel="manifest"]');
                        if (link) {
                              const href = link.href;
                              link.href = "";
                              link.href = href + "?t=" + Date.now();
                        }

                        notification.add("PWA manifest refreshed", { type: "info" });
                  },

                  getColorContrastRatio(color1, color2) {
                        // Calculate contrast ratio between two colors
                        const getLuminance = (hex) => {
                              const rgb = parseInt(hex.slice(1), 16);
                              const r = (rgb >> 16) & 0xff;
                              const g = (rgb >> 8) & 0xff;
                              const b = rgb & 0xff;

                              const [rs, gs, bs] = [r, g, b].map((c) => {
                                    c /= 255;
                                    return c <= 0.03928 ? c / 12.92 : Math.pow((c + 0.055) / 1.055, 2.4);
                              });

                              return 0.2126 * rs + 0.7152 * gs + 0.0722 * bs;
                        };

                        const l1 = getLuminance(color1);
                        const l2 = getLuminance(color2);
                        const lighter = Math.max(l1, l2);
                        const darker = Math.min(l1, l2);

                        return (lighter + 0.05) / (darker + 0.05);
                  },

                  suggestTextColor(backgroundColor) {
                        // Suggest white or black text based on background
                        const ratio = this.getColorContrastRatio(backgroundColor, "#FFFFFF");
                        return ratio >= 4.5 ? "#FFFFFF" : "#000000";
                  },
            };
      },
};

// Register the service
registry.category("services").add("pwaPreview", pwaPreviewService);

/**
 * Export utilities for external use
 */
export const PWAUtils = {
      /**
       * Convert hex color to RGB
       */
      hexToRgb(hex) {
            const result = /^#?([a-f\d]{2})([a-f\d]{2})([a-f\d]{2})$/i.exec(hex);
            return result
                  ? {
                        r: parseInt(result[1], 16),
                        g: parseInt(result[2], 16),
                        b: parseInt(result[3], 16),
                  }
                  : null;
      },

      /**
       * Convert RGB to hex
       */
      rgbToHex(r, g, b) {
            return "#" + [r, g, b].map((x) => x.toString(16).padStart(2, "0")).join("");
      },

      /**
       * Generate complementary color
       */
      getComplementaryColor(hex) {
            const rgb = this.hexToRgb(hex);
            if (!rgb) return hex;

            return this.rgbToHex(255 - rgb.r, 255 - rgb.g, 255 - rgb.b);
      },

      /**
       * Darken a color by percentage
       */
      darkenColor(hex, percent) {
            const rgb = this.hexToRgb(hex);
            if (!rgb) return hex;

            const factor = 1 - percent / 100;
            return this.rgbToHex(
                  Math.round(rgb.r * factor),
                  Math.round(rgb.g * factor),
                  Math.round(rgb.b * factor)
            );
      },

      /**
       * Lighten a color by percentage
       */
      lightenColor(hex, percent) {
            const rgb = this.hexToRgb(hex);
            if (!rgb) return hex;

            const factor = percent / 100;
            return this.rgbToHex(
                  Math.round(rgb.r + (255 - rgb.r) * factor),
                  Math.round(rgb.g + (255 - rgb.g) * factor),
                  Math.round(rgb.b + (255 - rgb.b) * factor)
            );
      },
};

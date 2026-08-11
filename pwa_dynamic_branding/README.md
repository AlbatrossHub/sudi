# Dynamic PWA Branding for Odoo 19

🚀 **Transform your Odoo instance into a fully branded Progressive Web App!**

## ✨ Features

### 🎨 Dynamic App Branding

- **App Name & Short Name** - Customize what users see when they install your app
- **Custom Icons** - Upload 192x192 and 512x512 icons for different devices
- **Maskable Icons** - Support for adaptive icons on Android
- **Apple Touch Icons** - Perfect iOS home screen icons

### 🎨 Theme & Colors

- **Theme Color** - Browser UI color (address bar, etc.)
- **Background Color** - Splash screen background
- **Brand Color Palette** - Primary, secondary, and accent colors
- **Live Preview** - See changes in real-time before applying

### 🚀 Splash Screen Customization

- **Custom Splash Image** - Your logo or branding image
- **Splash Background Color** - Match your brand
- **Tagline** - Short text shown during loading
- **Logo Toggle** - Show/hide logo on splash

### ⚙️ PWA Behavior

- **Display Mode** - Fullscreen, Standalone, Minimal UI, or Browser
- **Orientation** - Lock to portrait, landscape, or allow any
- **Start URL** - Where the app opens when launched
- **Scope** - Navigation boundaries for the PWA

### 📴 Offline Mode

- **Custom Offline Page** - Branded offline experience
- **Offline Title & Message** - Customizable text
- **Service Worker Support** - Enhanced offline capabilities

### 🌐 Customer Portal PWA

- **Separate Portal Branding** - Different app for customers
- **Portal-Specific Icons** - Unique icons for portal
- **Portal Theme** - Different colors for customer-facing app

### 🤖 AI Branding Generator

- **Industry-Based Suggestions** - Colors based on your industry
- **Brand Personality Matching** - Professional, playful, luxurious, etc.
- **Instant Preview** - See AI suggestions before applying
- **One-Click Apply** - Apply generated branding instantly

## 📦 Installation

1. Download and extract the module to your Odoo addons folder
2. Update the app list: `Apps > Update Apps List`
3. Search for "Dynamic PWA Branding" and install
4. Go to `Settings > General Settings > Progressive Web App`

## ⚙️ Configuration

### Basic Setup

1. Navigate to **Settings > General Settings**
2. Scroll to **Progressive Web App** section
3. Enter your **App Name** and **Short Name**
4. Upload your **512x512 icon**
5. Click **Generate All Sizes** to auto-create other icon sizes
6. Choose your **Theme** and **Background** colors
7. Save settings

### Using AI Branding Generator

1. Enable **AI Branding Generator** in settings
2. Click **Generate AI Branding**
3. Describe your brand or select industry/personality
4. Click **Generate Preview**
5. Review the suggested colors
6. Click **Apply Branding**

### Customer Portal PWA

1. Enable **Customer Portal PWA**
2. Set **Portal App Name**
3. Upload portal-specific icons
4. Configure portal theme colors
5. Save settings

## 🛠️ Technical Details

### Files Structure

```
pwa_dynamic_branding/
├── __init__.py
├── __manifest__.py
├── controllers/
│   ├── __init__.py
│   └── webmanifest.py          # Dynamic manifest generation
├── models/
│   ├── __init__.py
│   ├── pwa_config.py           # Main configuration model
│   ├── res_config_settings.py  # Settings integration
│   └── ai_branding_generator.py # AI branding logic
├── views/
│   ├── pwa_config_views.xml
│   └── res_config_settings_views.xml
├── security/
│   ├── pwa_security.xml
│   └── ir.model.access.csv
├── data/
│   └── pwa_default_data.xml
└── static/
    ├── description/
    │   └── icon.png
    └── src/
        ├── css/
        │   └── pwa_settings.css
        └── js/
            └── pwa_preview.js
```

### API Endpoints

- `GET /web/manifest.webmanifest` - Main PWA manifest (dynamic)
- `GET /pwa/icon/<size>` - Custom PWA icons
- `GET /pwa/apple-touch-icon.png` - iOS icon
- `GET /pwa/splash` - Custom splash image
- `GET /my/manifest.webmanifest` - Portal PWA manifest
- `POST /pwa/config` - Get current PWA config (JSON)

### Models

- `pwa.config` - Main PWA configuration storage
- `pwa.ai.branding` - AI branding generation records
- `pwa.ai.branding.wizard` - AI branding wizard

## 🔧 Dependencies

- `base`
- `web`
- `base_setup`

## 📝 Changelog

### v19.0.1.0.0 (Initial Release)

- Dynamic PWA manifest generation
- Custom icon support (192x192, 512x512, maskable)
- Theme and background color customization
- Splash screen customization
- Offline mode configuration
- Customer portal PWA support
- AI-powered branding generator
- Live preview in settings
- Multi-company support

## 🤝 Support

For support, please contact:

## 📄 License

This module is licensed under LGPL-3.

---

Made with ❤️ by **Aura Odoo Tech**

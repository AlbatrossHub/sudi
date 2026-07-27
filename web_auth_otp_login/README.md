# Web WhatsApp OTP Login

## Description
This Odoo module introduces seamless passwordless authentication for Odoo e-commerce and portal websites. Users can securely log in using just their phone number. A dynamic One-Time Password (OTP) is sent directly to their WhatsApp, ensuring both convenience and security.

This module restricts internal employees to use the standard email/password login method, ensuring that your backend remains secure, while giving your customers an effortless frontend login experience.

## Key Features
* **WhatsApp Integration**: Utilizes the official Meta WhatsApp API to send secure OTPs.
* **Auto-Registration**: Automatically creates a guest partner and user account for new phone numbers.
* **Smart Country Code**: Auto-detects the user's country code based on IP or Timezone for faster entry.
* **Native Bug Fix Included**: Includes a native Odoo bug fix that allows you to submit Meta authentication templates for approval directly from Odoo.
* **Security First**: Internal employees are restricted to email/password, keeping your backend secure. OTPs expire in 5 minutes.

## Installation
1. Clone or download this repository.
2. Place the `web_auth_otp_login` folder in your Odoo custom addons directory.
3. Update your Odoo apps list.
4. Search for "Web WhatsApp OTP Login" and click Install.

## Configuration
1. Ensure the official Odoo `whatsapp` module is installed and properly configured with your Meta Developer account.
2. An Authentication template must be created and approved in your WhatsApp Business account. Alternatively this module automatically creates a new template with the name 'OTP Authentication Template', You are required to mark "Submit for Approval"
3. The module automatically extends the login and registration pages to include the OTP flow.

## License
This module is currently licensed under **LGPL-3** (GNU Lesser General Public License v3.0).

*Note: If you plan to sell this module on the Odoo App Store and do not want others to be able to freely redistribute or copy the code, you should change the license to **OPL-1** (Odoo Proprietary License).*

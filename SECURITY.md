# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 0.x     | :white_check_mark: |

## Reporting a Vulnerability

We take the security of ERP Core seriously. If you discover a security vulnerability, please report it responsibly.

**Do NOT open a public GitHub issue for security vulnerabilities.**

Instead, please email **hello@kodlyft.com** with:

- A description of the vulnerability
- Steps to reproduce the issue
- The potential impact
- Any suggested fix (if you have one)

### What to Expect

- **Acknowledgment**: We will acknowledge receipt of your report within **48 hours**.
- **Assessment**: We will assess the severity and impact within **5 business days**.
- **Resolution**: We aim to release a fix within **30 days** of confirmation, depending on complexity.
- **Credit**: We will credit you in the release notes (unless you prefer to remain anonymous).

### Scope

The following are in scope:

- The ERP Core application code (Python backend)
- Authentication and authorization logic
- Data handling and storage
- API endpoints exposed by the app

The following are out of scope:

- Vulnerabilities in Frappe Framework or ERPNext (report these to their respective projects)
- Issues in third-party dependencies (report upstream, but let us know so we can update)
- Social engineering attacks
- Denial of service attacks

## Security Best Practices for Deployers

- Always run ERP Core behind HTTPS
- Keep Frappe, ERPNext, and ERP Core updated to the latest versions
- Use strong passwords and enable two-factor authentication
- Review user permissions and roles regularly

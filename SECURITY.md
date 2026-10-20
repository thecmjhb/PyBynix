# Security

Do not submit passwords, tokens, cookies, browser profiles or unredacted captures in issues.
Revoke any exposed session using the platform's account controls. Environment variables
and exported session files are secrets; this SDK does not provide encrypted storage.

HTTP redirects are not followed. Imported sessions are bound to one trusted host. TLS
verification stays enabled. Data methods do not require an order; demo is the default
account, real trading requires explicit opt-in. Never blindly resend an uncertain order.

Report a vulnerability privately to the maintainer using the repository's configured
private reporting channel when available; no private contact endpoint is invented here.

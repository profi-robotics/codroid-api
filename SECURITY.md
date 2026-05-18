# Security Policy

This repository is intended to be public. Do not commit:

- `.env` files with live controller credentials
- raw browser captures such as `*.har`
- cookies, user codes, tokens, or session IDs
- customer/site-specific robot project exports
- private network topology notes beyond generic examples

The client can issue real robot motion, power, mode, and project execution
commands. Only run examples on robot cells you are authorized to operate, keep
speed low while validating, and verify that physical stop controls are working.

Report suspected credential exposure or unsafe controller behavior privately to
the maintainers before opening a public issue.

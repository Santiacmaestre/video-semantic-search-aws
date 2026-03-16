# Semgrep Security Scan Report

## Scan Metadata

| Field | Value |
|-------|-------|
| **Date** | 2026-03-16 |
| **Semgrep Version** | 1.155.0 |
| **Configuration** | `--config auto` (Semgrep Registry — includes p/default, p/security-audit, p/secrets) |
| **Repository** | `video-semantic-search-w-nove-mme` |
| **Branch** | `dev` (commit `2d05365`) |
| **Files Scanned** | 59 |
| **Total Findings** | 13 |
| **Excluded Dirs** | `cdk.out/`, `node_modules/`, `.venv/`, `temp/` |

## Executive Summary

| Severity | Count |
|----------|------:|
| ERROR | 12 |
| WARNING | 1 |

**Overall Assessment: No actionable findings.** All 13 findings are false positives or not applicable given the project architecture and existing mitigations.

## Findings by Rule

| Rule ID | Severity | Count | Verdict |
|---------|----------|------:|---------|
| `insecure-document-method` | ERROR | 11 | False positive — all values sanitized with `escapeHtml()` |
| `missing-user` | ERROR | 1 | Not applicable — AWS Lambda ignores USER directive |
| `missing-integrity` | WARNING | 1 | False positive — inline `data:` URI, not external resource |

---

## Detailed Findings

### 1x `missing-user` (ERROR)

**Rule:** `dockerfile.security.missing-user.missing-user`

**Description:** By not specifying a USER, a program in the container may run as 'root'. This is a security hazard. If an attacker can control a process running as root, they may have control over the container. Ensure that the last USER in a Dockerfile is a USER other than 'root'.

**Locations:**

| File | Line |
|------|-----:|
| `deployment/Dockerfile` | 26 |

**Risk Assessment: NOT APPLICABLE**

This Dockerfile is used exclusively as an AWS Lambda container image (`lambda_.DockerImageFunction` in CDK). AWS Lambda runs containers in a micro-VM (Firecracker) with its own security boundary. The Lambda runtime ignores the `USER` directive — the container process always runs as the Lambda execution role regardless of the Dockerfile `USER` setting.

Adding `USER non-root` (as Semgrep's autofix suggests) would cause the Lambda function to fail because the Lambda runtime requires specific file permissions that a non-root user cannot access.

**Recommendation:** No code changes. Suppress with inline comment if needed.

### 1x `missing-integrity` (WARNING)

**Rule:** `html.security.audit.missing-integrity.missing-integrity`

**Description:** This tag is missing an 'integrity' subresource integrity attribute. The 'integrity' attribute allows for the browser to verify that externally hosted files (for example from a CDN) are delivered without unexpected manipulation. Without this attribute, if an attacker can modify the externally hosted resource, this could lead to XSS and other types of attacks. To prevent this, include the base64-encoded cryptographic hash of the resource (file) you're telling the browser to fetch in the 'integrity' attribute for all externally hosted files.

**Locations:**

| File | Line |
|------|-----:|
| `frontend-static/index.html` | 8 |

**Risk Assessment: FALSE POSITIVE**

The flagged tag is a `<link rel="icon">` element using an inline `data:` URI SVG:

```html
<link rel="icon" type="image/svg+xml" href="data:image/svg+xml,...">
```

Subresource Integrity (SRI) attributes are designed for externally-hosted resources fetched over the network (CDN scripts, stylesheets). A `data:` URI is inline content embedded directly in the HTML — there is no external fetch, no network request, and no integrity to verify. SRI cannot be applied to data URIs per the W3C specification.

**Recommendation:** No code changes needed.

### 11x `insecure-document-method` (ERROR)

**Rule:** `javascript.browser.security.insecure-document-method.insecure-document-method`

**Description:** User controlled data in methods like `innerHTML`, `outerHTML` or `document.write` is an anti-pattern that can lead to XSS vulnerabilities

**Locations:**

| File | Line |
|------|-----:|
| `frontend-static/js/app.js` | 95 |
| `frontend-static/js/app.js` | 132 |
| `frontend-static/js/app.js` | 247 |
| `frontend-static/js/app.js` | 294 |
| `frontend-static/js/app.js` | 314 |
| `frontend-static/js/app.js` | 464 |
| `frontend-static/js/app.js` | 498 |
| `frontend-static/js/app.js` | 580 |
| `frontend-static/js/app.js` | 608 |
| `frontend-static/js/app.js` | 670 |
| `frontend-static/js/app.js` | 708 |

**Risk Assessment: FALSE POSITIVE**

All 11 instances of `innerHTML` assignment in `app.js` use the project's `escapeHtml()` utility function to sanitize every user-controlled value before insertion into HTML templates. This function is defined in `app.js` and escapes `&`, `<`, `>`, `"`, and `'` characters, which is the standard mitigation for reflected XSS in innerHTML contexts.

Semgrep's `insecure-document-method` rule cannot perform taint tracking through template literals with function calls, so it flags all `innerHTML` usage regardless of sanitization. The CLAUDE.md project documentation explicitly notes this pattern:

> *"Frontend uses `escapeHtml()` on ALL user-controlled data in innerHTML contexts — always use it for new dynamic content"*

**Recommendation:** No code changes needed. Add `nosemgrep` comments or a `.semgrepignore` rule if clean CI scans are desired.

---

## Recommendations

| # | Action | Priority | Effort |
|---|--------|----------|--------|
| 1 | No code fixes required — all findings are false positives or N/A | — | — |
| 2 | Optionally add `.semgrepignore` or inline `// nosemgrep` comments for clean CI output | Low | 5 min |
| 3 | Consider adding Semgrep to CI/CD pipeline with these suppressions for ongoing scanning | Low | 30 min |

---

*Report generated by Semgrep 1.155.0 with `--config auto` on 2026-03-16.*

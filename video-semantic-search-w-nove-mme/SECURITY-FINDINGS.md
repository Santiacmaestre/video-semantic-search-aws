# Security Audit Findings

**Date:** 2026-03-12
**Scope:** CDK infrastructure (`cdk/`) and frontend (`frontend-static/`)
**Status:** Open — no fixes applied yet

---

## Summary

| Category | Critical | High | Medium | Low | Total |
|----------|----------|------|--------|-----|-------|
| CDK Infrastructure | 3 | 5 | 2 | 0 | 10 |
| Frontend | 3 | 4 | 1 | 1 | 9 |
| **Total** | **6** | **9** | **3** | **1** | **19** |

**Key Risk:** Multiple XSS entry points + localStorage token storage + no CSP creates a critical authentication bypass chain. An attacker controlling any user-facing field (project name, filename, entity name) can steal all auth tokens.

---

## CDK Infrastructure Findings

### CDK-01: S3 Vectors Wildcard IAM Permissions [CRITICAL]

**File:** `cdk/components/compute.py:74-76`

```python
self.lambda_role.add_to_policy(iam.PolicyStatement(
    actions=["s3vectors:*"],
    resources=["*"],
))
```

**Risk:** Any Lambda can read/write/delete vectors in any S3 Vectors index across the entire account, not just project-scoped indices.

**Remediation:** Scope actions and resources to project-specific indices:
```python
self.lambda_role.add_to_policy(iam.PolicyStatement(
    actions=["s3vectors:GetVectors", "s3vectors:PutVectors", "s3vectors:DeleteVectors",
             "s3vectors:CreateIndex", "s3vectors:DeleteIndex"],
    resources=[f"arn:aws:s3vectors:*:{account_id}:index/nova-*"],
))
```

---

### CDK-02: Bedrock Wildcard Model Access [CRITICAL]

**File:** `cdk/components/compute.py:83-91`

```python
self.lambda_role.add_to_policy(iam.PolicyStatement(
    actions=["bedrock:InvokeModel", "bedrock:StartAsyncInvoke",
             "bedrock:GetAsyncInvoke", "bedrock:ListAsyncInvokes"],
    resources=["*"],
))
```

**Risk:** Lambdas can invoke any Bedrock model in the account, including expensive ones. Only Nova MME and Claude Haiku are needed.

**Remediation:** Restrict to specific model ARNs from `config.py`.

---

### CDK-03: CORS Allows All Origins [CRITICAL]

**File:** `cdk/components/api.py:36-40`

```python
default_cors_preflight_options=apigw.CorsOptions(
    allow_origins=apigw.Cors.ALL_ORIGINS,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
),
```

**Risk:** Any website can make authenticated API requests from a user's browser, enabling CSRF-style attacks and data exfiltration.

**Remediation:** Restrict `allow_origins` to the CloudFront distribution domain.

---

### CDK-04: Rekognition Wildcard Resources [HIGH]

**File:** `cdk/components/compute.py:94-107`

**Risk:** Full Rekognition access (`DetectFaces`, `IndexFaces`, `DeleteFaces`, `CreateCollection`, `DeleteCollection`, etc.) on all resources. Can access or modify face collections from other projects.

**Remediation:** Separate celebrity recognition actions (which don't support resource-level permissions) from face collection actions (which do).

---

### CDK-05: Transcribe Wildcard Resources [HIGH]

**File:** `cdk/components/compute.py:110-117`

**Risk:** Can access transcription jobs from other projects.

**Remediation:** Scope to project-prefixed job names: `arn:aws:transcribe:*:{account_id}:transcription-job/nova-*`.

---

### CDK-06: Step Functions StartExecution on All Resources [HIGH]

**File:** `cdk/components/compute.py:120-123`

```python
self.lambda_role.add_to_policy(iam.PolicyStatement(
    actions=["states:StartExecution"],
    resources=["*"],
))
```

**Risk:** Can start execution of any state machine in the account.

**Remediation:** Pass the state machine ARN back after creation and add a scoped permission.

---

### CDK-07: OpenSearch Overly Permissive Access Policy [HIGH]

**File:** `cdk/components/search.py:38-50`

**Risk:** Uses `AnyPrincipal()` with only account-level condition and `es:*` on `domain/*`. Any IAM principal in the account gets full OpenSearch access.

**Remediation:** Restrict principal to the Lambda execution role ARN and scope to the specific domain.

---

### CDK-08: SQS Queues Missing Encryption [HIGH]

**File:** `cdk/components/storage.py:91-106`

**Risk:** Queue messages containing video metadata and S3 paths are stored unencrypted at rest.

**Remediation:** Add `encryption=sqs.QueueEncryption.SQS_MANAGED` to both queues.

---

### CDK-09: CloudFront Missing WAF Protection [MEDIUM]

**File:** `cdk/components/cdn.py:20-48`

**Risk:** No protection against common web attacks (DDoS, bot traffic, injection attempts).

**Remediation:** Attach a WAF WebACL with AWS managed rule groups (e.g., `AWSManagedRulesCommonRuleSet`).

---

### CDK-10: Cognito No MFA Enforcement [MEDIUM]

**File:** `cdk/components/auth.py:11-26`

**Risk:** Accounts protected only by passwords. Compromised passwords grant full access.

**Remediation:** Enable `mfa=cognito.Mfa.OPTIONAL` (or `REQUIRED`) with TOTP second factor.

---

## Frontend Findings

### FE-01: XSS in Project Name Display [CRITICAL]

**File:** `frontend-static/js/app.js:89`

```javascript
<h3>${p.name}</h3>
```

**Risk:** Project names containing HTML (e.g., `<img src=x onerror=alert(1)>`) execute as JavaScript when rendered via `innerHTML`.

**Remediation:** Use DOM methods (`textContent`) or add an HTML escape utility.

---

### FE-02: XSS in Search Results [CRITICAL]

**File:** `frontend-static/js/app.js:588-606`

**Risk:** Multiple fields injected into `innerHTML` without sanitization:
- `result.caption` (line 599)
- `result.genre` (line 600)
- `result.people[]` (line 589)
- `result.filename` (line 603)

An attacker who controls video metadata (via filenames, Rekognition output, or captions) can inject XSS.

**Remediation:** Escape all server-provided data before HTML insertion or use DOM element creation.

---

### FE-03: XSS in Entity Catalog [CRITICAL]

**File:** `frontend-static/js/app.js:690-708`

**Risk:** Entity names inserted into HTML content (line 700), `alt` attributes (line 695), and `onclick` handlers (lines 704-705) without proper escaping.

**Remediation:** Use `textContent` for display and proper encoding for all contexts.

---

### FE-04: XSS in Job Details Modal [HIGH]

**File:** `frontend-static/js/app.js:446-488`

**Risk:** `video.filename` and `data.genre` rendered without sanitization. Malicious filenames execute as JavaScript.

**Remediation:** Escape or use DOM methods.

---

### FE-05: XSS in Video Gallery [HIGH]

**File:** `frontend-static/js/app.js:652-662`

**Risk:** `video.filename` injected into both `src` attribute and HTML content without sanitization.

**Remediation:** Validate/escape filenames; use `textContent` for display.

---

### FE-06: Auth Tokens in localStorage [HIGH]

**File:** `frontend-static/js/auth.js:48-51`

```javascript
localStorage.setItem('idToken', authResult.IdToken);
localStorage.setItem('accessToken', authResult.AccessToken);
localStorage.setItem('refreshToken', authResult.RefreshToken);
```

**Risk:** `localStorage` is accessible to all JavaScript on the page. Combined with XSS vulnerabilities, attackers can steal all tokens including refresh tokens for persistent access.

**Remediation:** Implement CSP first (mitigates XSS). Long-term: consider httpOnly cookies for token storage (requires backend changes).

---

### FE-07: No Content Security Policy [HIGH]

**File:** `frontend-static/index.html`

**Risk:** No CSP meta tag or header. All XSS vulnerabilities can execute arbitrary JavaScript without restriction.

**Remediation:** Add CSP via CloudFront response headers or meta tag:
```html
<meta http-equiv="Content-Security-Policy"
  content="default-src 'self'; script-src 'self'; connect-src 'self' https://*.amazonaws.com;
           img-src 'self' data: https://*; media-src 'self' https://*; style-src 'self' 'unsafe-inline'">
```

---

### FE-08: Sensitive Data in Console Logs [MEDIUM]

**Files:** `frontend-static/js/app.js:339`, `frontend-static/js/auth.js:99`

**Risk:** Full error objects logged to console may contain Authorization headers or token fragments.

**Remediation:** Log only `error.message`, not the full error object.

---

### FE-09: Incomplete Logout Flow [LOW]

**File:** `frontend-static/js/auth.js:103-108`

**Risk:** Logout calls `window.location.reload()` without explicitly clearing all state first. Minor issue.

**Remediation:** Clear all localStorage and explicitly redirect to login screen.

---

## Recommended Fix Priority

| Priority | Items | Rationale |
|----------|-------|-----------|
| **P0 — Fix before production** | FE-01 to FE-03, FE-07, CDK-03 | XSS + no CSP + open CORS = token theft chain |
| **P1 — Fix soon** | FE-04 to FE-06, CDK-01, CDK-02, CDK-06 | Additional XSS, overly broad IAM |
| **P2 — Harden** | CDK-04, CDK-05, CDK-07, CDK-08, FE-08 | Least privilege, encryption |
| **P3 — Best practice** | CDK-09, CDK-10, FE-09 | WAF, MFA, logout cleanup |

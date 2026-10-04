# Owner-only browser uploads: assessment, 4 October 2026

**Status: not enabled or certified.** The existing public URL remains a synthetic
demo. Its landing page links to the free one-click Windows personal release.
No provider-rights or owner-gateway verification flags were changed.

## Feasible access architecture

Streamlit Community Cloud supports restricting app visibility to specific viewers,
including apps whose source repository is public. This is a platform-level option,
not authorization supplied by a UI password. Its documentation currently allows
only one private app per workspace; invited viewers can invite additional viewers
and see workspace analytics. Existing private applications must not be changed to
free a slot. Neither repository visibility nor the public demo is changed here.
[Official sharing documentation](https://docs.streamlit.io/deploy/streamlit-community-cloud/share-your-app).

For a distinct owner deployment, prefer a platform access gateway or separately
managed HTTPS/OIDC reverse proxy that authorizes exactly the owner's verified
identity. It must protect the complete app origin: HTML, HTTP uploads, WebSocket
upgrades, media, PDF downloads, redirects and alternate hosts. The backend must be
unreachable directly. Strip untrusted forwarded identity headers, restrict allowed
origins, enforce secure cookies/session expiry, and deny unknown identities before
the backend accepts a connection.

Streamlit's `st.login` supplies OIDC authentication; authorization is still needed.
Checking `st.user` in the page alone does not prove that media/download routes are
protected. The default identity-cookie lifetime is 30 days; logout does not
instantly invalidate other existing sessions. Native login also does not support
embedded apps. These require explicit design and validation rather than a checkbox
or environment flag.
[Official authentication documentation](https://docs.streamlit.io/develop/concepts/connections/authentication).

## Rights remain separate from authentication

yfinance identifies its Yahoo Finance API use as personal/research and directs
users to Yahoo's terms for downloaded-data rights. A library licence, noncommercial
project or owner allowlist is not a verified hosted retrieval/display grant.
Yahoo's legal endpoint denied access during this review; no restriction was
bypassed and no affirmative owner-hosting permission was inferred. OpenFIGI and
future derivative providers need their applicable terms reviewed separately.
[yfinance documentation](https://ranaroussi.github.io/yfinance/),
[Yahoo terms](https://legal.yahoo.com/ie/en/yahoo/terms/otos/index.html).

## Remaining deployment prerequisites

1. Establish applicable permission for exact owner-hosted Yahoo/FX/identification
   use, or select an authorized source with equivalent financial conventions.
2. Provision a separate owner backend/gateway with approved identity credentials,
   HTTPS origin and exact owner authorization. Keep secrets and private event
   evidence outside Git. No paid services have been provisioned.
3. Test logged-out, wrong-owner, expired, forged-header and direct-backend requests
   against upload, WebSocket and media/download routes. Inspect logs for private
   payloads; prove a private PDF cannot be fetched anonymously even with its full
   URL. Verify logout, connection expiry and private media cleanup.
4. Verify isolation, clear/delete behavior, orphan cleanup, storage access, input
   limits and aggregate request/concurrency quotas at the actual gateway. Existing
   worker limits alone are not hosted abuse protection.
5. Only after actual route/security and rights checks pass, configure `owner_hosted`
   and its operator attestations. Flags alone neither implement authentication nor
   demonstrate that a gateway was deployed.

External deployment/identity and data-rights evidence is unavailable in this
release. No unauthenticated upload configuration, tunnel, public personal mode,
scraper reactivation or hosted-authorization claim is shipped.

# Social Media Integration Centre

This app offers real OAuth connections and granular CEO-controlled publishing permissions. No account becomes connected without successful official OAuth authorization.

## Platform support

| Platform | Official connection | Publishing in this version |
|---|---|---|
| Facebook Pages | Meta OAuth | Text posts via Pages API |
| Instagram Professional linked to a Facebook Page | Meta OAuth | Single image feed posts from public HTTPS image URLs |
| YouTube | Google OAuth | Connect only; video upload not implemented |
| TikTok | TikTok Login Kit | Connect only; video/photo publishing not implemented |

Instagram personal accounts, Reels, Stories, carousels and video uploads are not supported. Provider app review and permissions may be required.

## Railway variables (server-side only)

SOCIAL_PUBLIC_BASE_URL — the public HTTPS origin, without a trailing path.
SOCIAL_TOKEN_ENCRYPTION_KEY — a persistent Fernet key.
SOCIAL_META_APP_ID / SOCIAL_META_APP_SECRET — Meta developer app.
SOCIAL_GOOGLE_CLIENT_ID / SOCIAL_GOOGLE_CLIENT_SECRET — Google Cloud OAuth Web Client.
SOCIAL_TIKTOK_CLIENT_KEY / SOCIAL_TIKTOK_CLIENT_SECRET — TikTok developer app.

Generate Fernet key locally: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Store all these as private Railway environment variables, never in GitHub or AI staff configuration. Do not share secrets through a chat. Replacing the Fernet key without re-encrypting accounts requires reconnection. Keep COOKIE_SECURE=true in production.

## Exact callback URLs for the current Railway domain

Meta: https://obscura-syndicate-office-production.up.railway.app/api/social/oauth/meta/callback

Google/YouTube: https://obscura-syndicate-office-production.up.railway.app/api/social/oauth/google/callback

TikTok: https://obscura-syndicate-office-production.up.railway.app/api/social/oauth/tiktok/callback

The public origin above should be the value of SOCIAL_PUBLIC_BASE_URL. Change all registered redirect URLs if your Railway domain changes. TikTok requires exact static HTTPS redirect.

## Provider setup

**Meta:** Register a Meta developer app and web Login flow. Request pages_show_list, pages_read_engagement, pages_manage_posts, instagram_basic and instagram_content_publish. The connected CEO must have CREATE_CONTENT capability on the relevant Facebook Page. Link any Instagram Professional account to that Page. Complete app review or advanced access if required for your account.

**Google:** Create Google Cloud OAuth Web Client and enable YouTube Data API v3. Configure consent screen and allow scopes youtube.readonly and youtube.upload. The app connects YouTube channels but has no implemented media upload. Tokens may expire and require reconnect.

**TikTok:** Register developer app, enable Login Kit, register HTTPS redirect, request user.info.basic and video.publish scopes. Publishing requires Content Posting API integration and provider app audit; not available in this release.

Official docs:
- https://developers.facebook.com/docs/graph-api/reference/page/feed/
- https://www.postman.com/meta/instagram/documentation/6yqw8pt/instagram-api
- https://developers.google.com/youtube/data/guides/authentication
- https://developers.tiktok.com/docs/en/login-kit-web
- https://developers.tiktok.com/docs/en/content-posting-api-reference-direct-post

## AI staff publishing permissions

Every staff/account pair defaults to **OFF**.

- OFF: cannot create or publish posts for that account.
- CEO Approval Required: can prepare attributed draft, but CEO must approve the content before publishing.
- Direct Publish: authorized draft is immediately eligible for publishing. In this release CEO still clicks Publish now in the Publishing Desk; autonomous background publishing and independent staff logins are not implemented.

Permissions are enforced server-side for the exact staff member and social account. No rights to unrelated profiles, ads or direct messages. Revoking permission blocks unpublished posting. If permission is downgraded to approval required, existing direct drafts need new approval.

## Publishing

After connecting via OAuth, CEO grants staff rights, creates a draft or imports a previously CEO-approved AI deliverable, and follows the explicit approval/publish steps in Publishing Desk.

Facebook: text post to Page.
Instagram: caption plus publicly reachable HTTPS image URL.

Provider success records the returned post ID. In ambiguous failure cases the post is marked Needs verification to avoid duplicate retries; check the platform manually.

Disconnect erases encrypted local credentials and grants and cancels queued posts. For remote OAuth app revocation, use provider security settings as well.

OAuth state is one-time and bound to CEO browser session. Tokens are encrypted using Fernet and never sent back to the browser. Production proxies should avoid logging query strings for OAuth callbacks.

No social account, post, developer app or authorization has yet been created by merely merging this code into GitHub.

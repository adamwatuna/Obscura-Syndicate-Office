# Social Media Integration Centre

This app offers real OAuth connections and granular CEO-controlled publishing permissions. No account becomes connected without successful official OAuth authorization.

## Platform support

| Platform | Official connection | Publishing in this version |
|---|---|---|
| Facebook Pages | Meta OAuth | Text posts via Pages API |
| Instagram Professional (Business or Creator), no Facebook Page | Instagram Direct Login | Single image feed posts from public HTTPS image URLs |
| Instagram Professional linked to Facebook Page | Legacy Meta OAuth | Single image feed posts from public HTTPS image URLs |
| YouTube | Google OAuth | Connect only; video upload not implemented |
| TikTok | TikTok Login Kit | Connect only; video/photo publishing not implemented |

Instagram personal accounts, Reels, Stories, carousels and video uploads are not supported. Provider app review and permissions may be required.

## Railway variables (server-side only)

SOCIAL_PUBLIC_BASE_URL — the public HTTPS origin, without a trailing path.
SOCIAL_TOKEN_ENCRYPTION_KEY — a persistent Fernet key.
SOCIAL_INSTAGRAM_APP_ID / SOCIAL_INSTAGRAM_APP_SECRET — Instagram app ID and app secret from Meta Developer dashboard > Instagram > API setup with Instagram login. These differ from the general Facebook app ID/secret.
SOCIAL_META_APP_ID / SOCIAL_META_APP_SECRET — only needed if connecting Facebook Pages through Facebook Login.
SOCIAL_GOOGLE_CLIENT_ID / SOCIAL_GOOGLE_CLIENT_SECRET — Google Cloud OAuth Web Client.
SOCIAL_TIKTOK_CLIENT_KEY / SOCIAL_TIKTOK_CLIENT_SECRET — TikTok developer app.

Generate Fernet key locally: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"

Store all these as private Railway environment variables, never in GitHub or AI staff configuration. Do not share secrets through a chat. Replacing the Fernet key without re-encrypting accounts requires reconnection. Keep COOKIE_SECURE=true in production.

## Exact callback URLs for the current Railway domain

Instagram Direct Login: https://obscura-syndicate-office-production.up.railway.app/api/social/oauth/instagram/callback

Meta: https://obscura-syndicate-office-production.up.railway.app/api/social/oauth/meta/callback

Google/YouTube: https://obscura-syndicate-office-production.up.railway.app/api/social/oauth/google/callback

TikTok: https://obscura-syndicate-office-production.up.railway.app/api/social/oauth/tiktok/callback

The public origin above should be the value of SOCIAL_PUBLIC_BASE_URL. Change all registered redirect URLs if your Railway domain changes. TikTok requires exact static HTTPS redirect.

## Provider setup

**Instagram Direct Login — recommended if you want to skip Facebook Page:** On https://developers.facebook.com/apps/ create a Meta developer app with the Instagram use case (Manage messaging & content on Instagram). In the app dashboard open Instagram > API setup with Instagram login > Business login settings. Register the exact Instagram Direct Login callback URL shown above, then take the **Instagram App ID / Instagram App Secret** shown under Instagram API setup (not App Settings > Basic). Grant the Instagram professional account the developer/tester role as needed. Request instagram_business_basic and instagram_business_content_publish scopes. Add the corresponding 4 variables in Railway: SOCIAL_PUBLIC_BASE_URL, SOCIAL_TOKEN_ENCRYPTION_KEY, SOCIAL_INSTAGRAM_APP_ID, SOCIAL_INSTAGRAM_APP_SECRET. Redeploy. In Obscura Integration Centre choose **Instagram · Direct Login (no Facebook Page)** and authorize with your Instagram Business/Creator account. No Facebook Page or Facebook Login is needed. Meta App Review/Advanced Access may be required for other account owners. Refresh the long-lived Instagram token before it expires (typically about 60 days) using the account's Refresh Instagram access control; an expired token requires reconnect.

**Meta legacy Facebook Login:** Register a Meta developer app and web Login flow. Request pages_show_list, pages_read_engagement, pages_manage_posts, instagram_basic and instagram_content_publish. The connected CEO must have CREATE_CONTENT capability on the relevant Facebook Page. Link any Instagram Professional account to that Page. Complete app review or advanced access if required for your account.

**Google:** Create Google Cloud OAuth Web Client and enable YouTube Data API v3. Configure consent screen and allow scopes youtube.readonly and youtube.upload. The app connects YouTube channels but has no implemented media upload. Tokens may expire and require reconnect.

**TikTok:** Register developer app, enable Login Kit, register HTTPS redirect, request user.info.basic and video.publish scopes. Publishing requires Content Posting API integration and provider app audit; not available in this release.

Official docs:
- https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/business-login
- https://developers.facebook.com/docs/instagram-platform/instagram-api-with-instagram-login/content-publishing
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

# Publishing with Postiz

Studio publishes through a self-hosted [Postiz](https://docs.postiz.com/) instance (Docker Compose).

## Connect Studio to Postiz
In `.env`:
```
POSTIZ_URL=http://localhost:4007/api/public/v1      # API base (local is fastest)
POSTIZ_API_KEY=...                                   # Postiz → Settings → Public API
POSTIZ_PUBLIC_URL=https://postiz.example.com         # public HTTPS address of Postiz
```

## Give Postiz a public HTTPS address
Social platforms only redirect logins to public HTTPS URLs, and Instagram downloads the video from Postiz's
public URL. With Cloudflare:
```bash
cloudflared tunnel login                      # approve in the browser, pick your domain
cloudflared tunnel create content-studio
cloudflared tunnel route dns content-studio postiz.example.com
cloudflared tunnel route dns content-studio studio.example.com   # optional: the dashboard too
```
`/etc/cloudflared/config.yml`:
```yaml
tunnel: <tunnel-id>
credentials-file: /etc/cloudflared/<tunnel-id>.json
ingress:
  - hostname: postiz.example.com
    service: http://localhost:4007
  - hostname: studio.example.com
    service: http://localhost:8794
  - service: http_status:404
```
`sudo cloudflared --config /etc/cloudflared/config.yml service install`

In Postiz's `docker-compose.yaml` set `MAIN_URL`, `FRONTEND_URL` to the public address,
`NEXT_PUBLIC_BACKEND_URL` to `<public address>/api`, and **`DISABLE_REGISTRATION: 'true'`** (after your
account exists), then `docker compose up -d postiz`.

## Connect platforms
Self-hosted Postiz uses your own developer app per platform:
1. Create the app (TikTok, Meta for Instagram + Facebook, Google Cloud for YouTube, LinkedIn, X).
2. Redirect URL: `https://postiz.example.com/integrations/social/<platform>`.
3. Put the client ID/secret into Postiz's compose env (`TIKTOK_CLIENT_ID`, `FACEBOOK_APP_ID`, …) and restart.
4. In Postiz → Add channel → log in. Channels appear in Studio → Publish.

Notes: TikTok only allows private posts until the app passes TikTok's audit; Instagram must be a
Business/Creator account linked to a Facebook Page; X video posting needs a paid API tier.

Rate limits (Postiz public API): create 90/h, list 30/h — Studio syncs status at most every 10 minutes and
only when a post is due.

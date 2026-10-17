# Embedding the advising chatbot in Canvas

SJSU Canvas is served from `https://sjsu.instructure.com`. The hosted chat
page is `https://ischooladvisingchatbot-dx4ynl.azurewebsites.net/`.

That page sends:

```
Content-Security-Policy: frame-ancestors 'self' https://sjsu.instructure.com https://*.instructure.com
```

`https://*.instructure.com` matches one host label, including
`sjsu.instructure.com`. It does not match a host with an extra label. There
is no `X-Frame-Options: DENY` or `SAMEORIGIN` on this page, because either
value would block Canvas. Other responses, including `/api/`, `/healthz`,
`/readyz`, and `/evaluation`, still send `X-Frame-Options: DENY` and
`frame-ancestors 'none'`.

`EMBED_ALLOWED_ORIGINS` replaces that list. Separate sources with spaces or
commas. Set it to `'none'` to refuse framing on the chat page again. An
invalid value is ignored, a warning is logged, and the Canvas list above is
kept.

## Redirect link

In the blueprint course, add an External URL module item or a link that
opens the chatbot:

```
https://ischooladvisingchatbot-dx4ynl.azurewebsites.net/?embed=1
```

`?embed=1` hides the large campus header so the chat fits a smaller frame.
Leave the query off to open the full page. A redirect item opens the app in
the browser and does not need the page to be framed. Prefer a new tab when
Canvas offers that choice.

## Iframe

On a blueprint course page, open the HTML editor and paste:

```html
<iframe
  src="https://ischooladvisingchatbot-dx4ynl.azurewebsites.net/?embed=1"
  title="SJSU iSchool advising assistant"
  width="100%"
  height="720"
  style="border: 0;"
></iframe>
```

The iframe only loads when the surrounding page is one of the allowed
frame ancestors. A page on another site cannot embed the chatbot.

## Session cookie inside the frame

Opening `/?embed=1` over HTTPS marks that browser session as an embed. The
session cookie for that session is `SameSite=None`, `Secure`, and
`Partitioned`, so the chat can keep its session inside the Canvas frame
without sending that cookie to unrelated sites. Visits that are not an embed
keep `SESSION_COOKIE_SAMESITE` (Lax unless that setting is changed).

Browsers reject `SameSite=None` without `Secure`. A plain HTTP request,
including local `http://localhost:5000/?embed=1`, therefore keeps the normal
SameSite setting. Canvas itself is HTTPS, and the hosted app is HTTPS.

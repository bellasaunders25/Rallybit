const ORIGIN = "https://raspberrypi.tail5cb034.ts.net";

function rewriteLocation(value, publicUrl) {
  if (!value) return value;

  // Keep path-relative redirects relative to the requested page. Resolving a
  // value such as "login.php" against the origin root would incorrectly turn
  // /dashboard/login.php into /login.php.
  if (!/^https?:\/\//i.test(value)) return value;

  try {
    const location = new URL(value, ORIGIN);
    const origin = new URL(ORIGIN);
    if (location.origin === origin.origin) {
      location.protocol = publicUrl.protocol;
      location.host = publicUrl.host;
    }
    return location.toString();
  } catch {
    return value;
  }
}

export default {
  async fetch(request) {
    const publicUrl = new URL(request.url);
    const originUrl = new URL(publicUrl.pathname + publicUrl.search, ORIGIN);
    const headers = new Headers(request.headers);

    headers.set("X-Forwarded-Host", publicUrl.host);
    headers.set("X-Forwarded-Proto", "https");
    headers.set("X-Rallybit-Edge", "cloudflare-pages");

    const upstreamRequest = new Request(originUrl, {
      method: request.method,
      headers,
      body: request.method === "GET" || request.method === "HEAD" ? undefined : request.body,
      redirect: "manual",
    });

    let upstream;
    try {
      upstream = await fetch(upstreamRequest);
    } catch {
      return new Response(
        "Rallybit is temporarily unavailable. Please try again in a moment.",
        {
          status: 502,
          headers: {
            "Cache-Control": "no-store",
            "Content-Type": "text/plain; charset=utf-8",
            "X-Content-Type-Options": "nosniff",
          },
        },
      );
    }

    const responseHeaders = new Headers(upstream.headers);
    const location = responseHeaders.get("Location");
    if (location) {
      responseHeaders.set("Location", rewriteLocation(location, publicUrl));
    }

    responseHeaders.set("X-Rallybit-Edge", "cloudflare-pages");

    return new Response(upstream.body, {
      status: upstream.status,
      statusText: upstream.statusText,
      headers: responseHeaders,
    });
  },
};

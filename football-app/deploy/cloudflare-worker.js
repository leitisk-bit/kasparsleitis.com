// Cloudflare Worker: sends kasparsleitis.com/football* to the app on the Ubuntu server
// (through the Cloudflare Tunnel hostname), while the rest of kasparsleitis.com stays on Namecheap.
//
// Setup: Workers & Pages -> Create Worker -> paste this -> Deploy
//        Worker -> Settings -> Triggers -> Add route:  kasparsleitis.com/football*
//        (and www.kasparsleitis.com/football* if you use www)
const ORIGIN = "https://football-origin.kasparsleitis.com"; // tunnel hostname pointing at http://localhost:8095

export default {
  async fetch(request) {
    const url = new URL(request.url);
    const target = ORIGIN + url.pathname + url.search;
    const init = {
      method: request.method,
      headers: request.headers,
      body: ["GET", "HEAD"].includes(request.method) ? undefined : request.body,
      redirect: "manual",
    };
    const resp = await fetch(target, init);
    // keep redirects on the public domain
    const headers = new Headers(resp.headers);
    const loc = headers.get("location");
    if (loc && loc.startsWith(ORIGIN)) headers.set("location", loc.replace(ORIGIN, url.origin));
    return new Response(resp.body, { status: resp.status, headers });
  },
};

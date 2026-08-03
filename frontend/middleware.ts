import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/**
 * - Landing stays light-forced.
 * - www → apex so OAuth state + session cookies always share one host
 *   (Domain=zivo.fyi also covers this; redirect keeps URLs canonical).
 */
export function middleware(request: NextRequest) {
  const host = request.headers.get("host")?.split(":")[0]?.toLowerCase() ?? "";
  if (host === "www.zivo.fyi") {
    // Build apex URL from path/query only. nextUrl.clone() keeps the container
    // listen port (:3000), which would leak into the public Location header.
    const dest = new URL(request.url);
    dest.protocol = "https:";
    dest.hostname = "zivo.fyi";
    dest.port = "";
    return NextResponse.redirect(dest, 308);
  }

  const response = NextResponse.next();
  if (request.nextUrl.pathname === "/") {
    response.headers.set("x-zivo-force-light", "1");
  }
  return response;
}

export const config = {
  matcher: [
    "/((?!_next/static|_next/image|favicon.ico|icon-|apple-touch-icon|manifest.webmanifest|logo.png).*)",
  ],
};

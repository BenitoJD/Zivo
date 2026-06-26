import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

/** Landing is always light; workspace/app routes respect the user theme cookie. */
export function middleware(request: NextRequest) {
  const response = NextResponse.next();
  if (request.nextUrl.pathname === "/") {
    response.headers.set("x-zivo-force-light", "1");
  }
  return response;
}

export const config = {
  matcher: ["/"],
};

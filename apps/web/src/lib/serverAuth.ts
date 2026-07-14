import "server-only";

import { cookies } from "next/headers";
import { AUTH_COOKIE_NAME, authorizationFromSessionCookie } from "./sessionCookie";

export async function getServerAuthorization() {
  const cookieStore = await cookies();
  return authorizationFromSessionCookie(cookieStore.get(AUTH_COOKIE_NAME)?.value);
}

import "server-only";

import {
  api,
  ApiError,
  configureServerAuthorizationProvider,
} from "./api";
import { getServerAuthorization } from "./serverAuth";

// getServerAuthorization reads Next's request-local cookie store on every call;
// no credential is captured in module state or shared between requests.
configureServerAuthorizationProvider(getServerAuthorization);

export { api, ApiError };

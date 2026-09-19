# 5. Authentication: JWT, public reads, staff-only writes

## Context

The assignment does not mention authentication, but a catalog whose write
endpoints are open cannot go to production.

## Decision

- **Reads are public, writes require a staff user** (`IsStaffOrReadOnly`).
  `IsAuthenticatedOrReadOnly` would be wrong for a shop: customers have accounts
  too, and being logged in must not allow editing products.
- **JWT (`djangorestframework-simplejwt`)** in the `Authorization: Bearer` header.
  The service is a stateless API that other services and a back-office SPA call;
  tokens need no shared session store and no cookies. No cookies also means no
  CSRF surface, which is why the session/CSRF middleware are not installed.
- Access tokens live 15 minutes, refresh tokens 1 day; both configurable.
- Tokens are signed with **HS256 and a dedicated key** (`JWT_SIGNING_KEY`), so
  rotating it does not disturb anything else that depends on `SECRET_KEY`.
- The token endpoints are **rate limited** (10/min per client by default) since
  they are where password guessing would happen. A client is the address of the
  connection. DRF's default is to believe the `X-Forwarded-For` header, which
  lets anyone reset their own allowance by sending a new value; here the header
  is ignored unless `NUM_PROXIES` says how many trusted proxies are in front.

## Consequences

- **A token cannot be revoked before it expires.** The short access lifetime
  bounds the damage. If immediate revocation is required: simplejwt's blacklist
  app with refresh-token rotation (a settings change plus one migration).
- HS256 means every verifier needs the secret. If other services should verify
  tokens themselves, switch to RS256 and publish the public key.
- Throttle counters live in Django's cache: per-process memory by default. The
  Docker image runs 3 workers, so the limit a client really gets is up to three
  times the configured one; a shared cache (Redis) makes it exact.
- Users are created with `manage.py createsuperuser` (Django's stock password
  rules apply); user management endpoints are out of scope.
- A first-party back-office on the same domain could equally use session
  cookies (HttpOnly, with CSRF protection). Supporting both later is one more
  entry in `DEFAULT_AUTHENTICATION_CLASSES`.

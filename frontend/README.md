# frontend/

This folder is where the React admin SPA is meant to live. In the zip you sent,
`frontend/src/` and `backend/services/` were **empty** — only `node_modules`
and `frontend/.env` survived, so there was no React app to build.

The Flask + Jinja admin panel and the customer portal are complete and do not
depend on anything in here. This folder is left in place so you can drop the
React app back in when you have it.

## The API it should talk to

The REST API it needs is fully rebuilt and live at `/api/v1` — 188 endpoints,
JWT authenticated. Point `VITE_API_BASE_URL` (in `frontend/.env`) at your
Flask host and it will work.

### Getting a token

```js
const res = await fetch(`${API}/api/v1/auth/staff/login`, {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ username, password }),
});
const { data } = await res.json();
// data.access_token, data.refresh_token, data.user, data.branding
```

Then send `Authorization: Bearer <access_token>` on every request. When a call
returns `401 token_expired`, POST the refresh token to
`/api/v1/auth/refresh` to get a new access token.

### Response shape

Success:

```json
{ "ok": true, "data": ..., "meta": { "page": 1, "per_page": 25, "total": 42, "pages": 2 } }
```

Failure:

```json
{ "ok": false, "error": "not_found" }
```

`meta` is present on any list endpoint. All list endpoints accept
`?page=` and `?per_page=` (max 200), and most accept `?q=` for search.

### The endpoint map

| Area | Base path |
|---|---|
| Auth | `/api/v1/auth/staff/*`, `/api/v1/auth/customer/*` |
| Dashboard | `/api/v1/dashboard`, `/api/v1/dashboard/summary` |
| Customers | `/api/v1/customers`, `/api/v1/customers/<id>`, `/api/v1/customers/<id>/ledger` |
| Plans | `/api/v1/plans`, `/api/v1/service-providers` |
| Invoices | `/api/v1/invoices`, `/api/v1/invoices/<id>` |
| Payments | `/api/v1/payments`, `/api/v1/payments/<id>/authorize`, `/reject` |
| Staff | `/api/v1/staff`, `/api/v1/staff-types` |
| Reports | `/api/v1/reports/{plan-expiry,attendance,leaves,payroll,collection,expenses}` |
| Masters | `/api/v1/masters/{zones,localities,areas,buildings,addresses,tax,addon-categories,message-templates}` |
| Expenses | `/api/v1/expenses`, `/api/v1/expenses/{categories,accounts,payees}` |
| Inventory | `/api/v1/inventory/{vendors,products,stock,vendor-bills}` |
| HR | `/api/v1/hr/{attendance,leaves,payroll}` |
| Company | `/api/v1/companies`, `/api/v1/branding` (public), `/api/v1/companies/<id>/logo` |
| Notifications | `/api/v1/notification-templates`, `/api/v1/notifications`, `/api/v1/notifications/send` |
| Settings | `/api/v1/settings`, `/api/v1/settings/{backups,export,import}` |
| Messaging | `/api/v1/messages/log`, `/api/v1/messages/bulk` |
| ISP | `/api/v1/isp/{credentials,test,sync-logs}` |
| Customer app | `/api/v1/portal/*` (dashboard, invoices, payments, plans, tickets, renew, change-plan, pay, notifications, device-token) |

Every master table gets the same five verbs:

```
GET    /api/v1/<slug>          ?q=&page=&per_page=
GET    /api/v1/<slug>/<id>
POST   /api/v1/<slug>
PUT    /api/v1/<slug>/<id>
DELETE /api/v1/<slug>/<id>     (admin only, 409 if the row is referenced)
```

### CORS

Set `CORS_ORIGINS` in the environment to a comma-separated list of the origins
your dev server and production build run from. The default already allows
`http://localhost:5173`, `http://127.0.0.1:5173` and `http://localhost:3000`.

### Health check

`GET /api/v1/health` needs no token and returns
`{"ok": true, "service": "unicrm-api", "version": "1.3"}`.

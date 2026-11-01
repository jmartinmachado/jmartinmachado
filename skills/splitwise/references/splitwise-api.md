# Splitwise API v3.0 — condensed catalog

as_of: 11-Oct-2026
source: https://dev.splitwise.com/ (OpenAPI 3.0.1 embedded in the Redoc page as
`__redoc_state.spec.data`; the `/swagger.json` and `/openapi.json` URLs return 404).
Cross-checks used only for the Unconfirmed section: https://splitwise.readthedocs.io/en/latest/
and https://github.com/namaggarwal/splitwise. Everything not prefixed `Assumption:` comes
from the official spec.

## Auth and limits

- Base URL: `https://secure.splitwise.com/api/v3.0`. Path `{id}` params are integers; GET
  filters go in the query string; every POST body in the spec is `application/json`.
- Two alternative schemes:
  - ApiKeyAuth: `Authorization: Bearer <api_key>` (http bearer, `bearerFormat: "API key"`).
    The key is generated on the app details page (https://secure.splitwise.com/apps) and is
    an access token for the personal account; revoke by generating a new one.
  - OAuth 2 authorization-code flow (`/oauth/authorize`, `/oauth/token`, no scopes).
- Rate limits: HTTP 429 when too many requests in a short period; limits vary by endpoint
  and change without notice. No `Retry-After` header is documented.
- Edge block (observed 11-Oct-2026, not in the docs): requests with the default
  `Python-urllib/3.x` User-Agent get HTTP 403 from Cloudflare, error 1010
  `browser_signature_banned`, `retryable: false`, before Splitwise sees the token. A
  custom User-Agent (`splitwise-skill/1.0`) is mandatory on every request.
- Terms: self-serve API is non-commercial only.
- Error bodies:
  - 401 `{"error": "Invalid API request: you are not logged in"}` (singular `error`)
  - 403 `{"errors": {"base": ["Invalid API request: you do not have permission to perform that action"]}}`
  - 404 `{"errors": {"base": ["Invalid API Request: record not found"]}}`
  - 400 `{"errors": {"base": ["Unrecognized parameter `bad_parameter`"]}}`
- Soft failures: many POSTs answer 200 with `success: false` or a non-empty `errors`.
  "200 OK does not indicate a successful response" — always check `errors` / `success`.

## Users

User object: `id`, `first_name`, `last_name` (nullable), `email`, `registration_status`
(confirmed|dummy|invited), `picture {small, medium, large}`, `custom_picture`.

- `GET get_current_user` → `{"user": user + notifications_read, notifications_count,
  notifications {...}, default_currency ("USD"), locale}`.
- `GET get_user/{id}` → `{"user": user}`. 401/403/404.
- `POST update_user/{id}` body (optional strings): first_name, last_name, email, password,
  locale, default_currency.

## Groups

Group object: `id`, `name`, `group_type` (home|trip|couple|other|apartment|house),
`updated_at`, `simplify_by_default`, `members[]`, `original_debts[]`, `simplified_debts[]`,
`avatar`, `cover_photo`, `invite_link`. There is NO group default currency field.
Member = user fields + `balance: [{"currency_code": "USD", "amount": "-5.02"}]` (string,
one entry per currency). Debt: `{"from": user_id, "to": user_id, "amount": "414.5",
"currency_code": "USD"}`.

- `GET get_groups` → `{"groups": [group]}`. Expenses outside any group belong to group ID 0.
- `GET get_group/{id}` → `{"group": group}`. 401/403/404.
- `POST create_group` body: `name` (required), `group_type`, `simplify_by_default`,
  members as `users__{i}__{user_id|first_name|last_name|email}`. Current user added by
  default. → `{"group": group}`.
- `POST delete_group/{id}` destroys all expenses → `{"success": bool}`.
- `POST undelete_group/{id}` → `{"success": true}` or `{"success": false, "errors": [..]}`
  (errors is an ARRAY here).
- `POST add_user_to_group` body `{"group_id", "user_id"}` or `{"group_id", "first_name",
  "last_name", "email"}` → `{"success", "user", "errors": {}}`.
- `POST remove_user_from_group` body `{"group_id", "user_id"}`; fails with
  `errors.base = ["The user has a non-zero balance"]`.

## Friends

Friend = user fields + `groups: [{"group_id", "balance": [...]}]` + `balance: [...]` +
`updated_at`.

- `GET get_friends` → `{"friends": [friend]}`.
- `GET get_friend/{id}` ({id} = user id) → `{"friend": friend}`.
- Also: `POST create_friend` `{user_email, user_first_name, user_last_name}`;
  `POST create_friends` (`users__{i}__*`); `POST delete_friend/{id}` → `{success, errors}`.

## Currencies and categories

- `GET get_currencies` → `{"currencies": [{"currency_code": "BRL", "unit": "R$"}]}`.
- `GET get_categories` → `{"categories": [{id, name, icon, icon_types, subcategories:
  [{id, name, icon, icon_types}]}]}`.
  Rule: `category_id` on create_expense must be a SUBCATEGORY id; for a bare parent use its
  "Other" subcategory.

## Expenses

Expense object: `id`, `group_id` (null outside a group), `friendship_id`,
`expense_bundle_id`, `description`, `repeats`, `repeat_interval`
(never|weekly|fortnightly|monthly|yearly), `email_reminder`, `email_reminder_in_advance`,
`next_repeat`, `details` (notes), `comments_count`, `payment` (settle-up between users),
`transaction_confirmed`, `cost` (string, "25.0"), `currency_code`, `repayments:
[{"from": owing_user_id, "to": owed_user_id, "amount": "25.0"}]`, `date` (date-time),
`created_at`, `created_by`, `updated_at`, `updated_by`, `deleted_at` (null when live),
`deleted_by`, `category {id, name}`, `receipt {large, original}`,
`users: [{"user": {id, first_name, last_name, picture}, "user_id", "paid_share",
"owed_share", "net_balance"}]` (shares are strings), `comments[]`.

- `GET get_expense/{id}` → `{"expense": expense}`.
- `GET get_expenses` query (all optional): `group_id` (when set, `friend_id` is ignored),
  `friend_id`, `dated_after`, `dated_before`, `updated_after`, `updated_before` (date-time),
  `limit` DEFAULT 20, `offset` DEFAULT 0 → `{"expenses": [expense]}`. Deleted expenses are
  not stated to be excluded; they carry a non-null `deleted_at`.
- `POST create_expense` — verbatim: "You may either split an expense equally (only with
  `group_id` provided), or supply a list of shares. When splitting equally, the
  authenticated user is assumed to be the payer. When providing a list of shares, each
  share must include `paid_share` and `owed_share`, and must be identified by one of the
  following: `email`, `first_name`, and `last_name` — or `user_id`. 200 OK does not
  indicate a successful response. The operation was successful only if `errors` is empty."
  - shared fields (none marked required): `cost` (string, decimal, max 2 places, "25"),
    `description`, `details`, `date` ("2012-05-02T13:00:00Z"), `repeat_interval`,
    `currency_code` (from get_currencies), `category_id` (int).
  - equal split: shared + `group_id` + `split_equally: true`.
  - by shares: shared + `group_id` (`0` = outside a group) + flattened keys
    `users__{i}__user_id`, `users__{i}__paid_share`, `users__{i}__owed_share` (strings, 2
    decimals), or `users__{i}__first_name` / `last_name` / `email` instead of user_id.
  - NOT in the request schema: `payment`, `receipt`, `email_reminder*`.
  - No rule relating `cost` to the share sums is written in the docs (see Unconfirmed).

```json
{
  "cost": "25",
  "description": "Grocery run",
  "date": "2012-05-02T13:00:00Z",
  "currency_code": "USD",
  "category_id": 15,
  "group_id": 0,
  "users__0__user_id": 54123,
  "users__0__paid_share": "25",
  "users__0__owed_share": "13.55",
  "users__1__first_name": "Neu",
  "users__1__last_name": "Yewzer",
  "users__1__email": "neuyewxyz@example.com",
  "users__1__paid_share": "0",
  "users__1__owed_share": "11.45"
}
```

  Success (200): `{"expenses": [expense], "errors": {}}`. Documented failure: 400
  `{"errors": {"base": ["Unrecognized parameter `bad_parameter`"]}}`; 401; 403.
- `POST update_expense/{id}` — "Parameters are the same as in `create_expense`, but you only
  need to include parameters that are changing. If any value is supplied for
  `users__{index}__{property}`, ALL shares for the expense will be overwritten." Body
  schema = by-shares shape only. → `{"expenses": [expense], "errors": {}}`.
- `POST delete_expense/{id}` → `{"success": true}` or `{"success": false, "errors":
  {"expense": ["does not exist, or has already been deleted"]}}`.
- `POST undelete_expense/{id}` → `{"success": bool}`.

## Comments

Comment: `id`, `content`, `comment_type` (System|User), `relation_type` (ExpenseComment),
`relation_id` (expense id), `created_at`, `deleted_at`, `user {id, first_name, last_name}`.

- `GET get_comments?expense_id=<id>` (required) → `{"comments": [comment]}`.
- `POST create_comment` body `{"expense_id", "content"}` → `{"comment": comment}`.
- `POST delete_comment/{id}` → `{"comment": comment}` (the deleted one).

## Notifications

- `GET get_notifications` query `updated_after` (date-time), `limit` DEFAULT 0 (= server
  maximum) → `{"notifications": [{id, type, created_at, created_by, source {type, id,
  url}, image_url, image_shape, content (HTML)}]}`. Types: 0 expense added, 1 updated,
  2 deleted, 3 comment added, 4 added to group, 5 removed from group, 6 group deleted,
  7 group settings changed, 8 added as friend, 9 removed as friend, 10 news, 11 debt
  simplification, 12 group undeleted, 13 expense undeleted, 14 group currency conversion,
  15 friend currency conversion.

## Unconfirmed

- Assumption: form-encoded bodies are accepted by every POST (the Python SDK sends them);
  the spec declares `application/json` only — this skill sends JSON.
- Assumption: `sum(paid_share) == cost` and `sum(owed_share) == cost` are enforced
  server-side. Not stated; the spec's own examples balance exactly. The script enforces
  both locally.
- Assumption: `payment` (bool) is accepted on create/update to mark a settle-up (SDK only).
- Assumption: a receipt image can be uploaded as a multipart `receipt` field (SDK only).
- Assumption: `visible` query param on get_expenses hides deleted expenses (SDK only).
- Assumption: no `Retry-After` on 429; back off a few seconds and retry once.
- Assumption: a 200 with non-empty `errors` carries `expenses: []` and `errors` in the
  `{"base": [...]}` / `{"<field>": [...]}` pattern.
- Assumption: decimal separator is `.`; "limited to 2 decimal places" is stated.
- Assumption: `date` accepts date-only or non-UTC offsets; only `...T13:00:00Z` is shown.
  The script always sends `YYYY-MM-DDT12:00:00Z`.
- Assumption: OAuth2 access tokens travel as `Authorization: Bearer <token>` too.
- Assumption: `update_user/{id}` answers `{"user": ...}` (spec shows a bare user).
- Assumption: `split_equally` with `group_id: 0` is rejected ("only with `group_id`").
- Assumption: when `category_id` is omitted the server assigns its default category.

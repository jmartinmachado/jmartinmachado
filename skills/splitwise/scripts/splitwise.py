#!/usr/bin/env python3
"""Splitwise CLI for the splitwise skill (stdlib only).

Reads SPLITWISE_API_KEY from the environment or ~/.config/splitwise/.env and never
writes it. Every write subcommand supports --dry-run; CONFIRM gates live in SKILL.md.
Exit codes: 0 ok, 1 API/validation error, 2 missing key, 3 unresolved name, 5 duplicate.
"""
import argparse
import datetime as dt
import json
import os
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from decimal import ROUND_DOWN, Decimal, InvalidOperation
from pathlib import Path


BASE = "https://secure.splitwise.com/api/v3.0"
ENV_PATH = Path.home() / ".config" / "splitwise" / ".env"
CENT = Decimal("0.01")
SELF_WORDS = {"yo", "me", "mi", "myself"}

BOOTSTRAP = """ERR>  SPLITWISE_API_KEY ausente
  1. Registrar una app en https://secure.splitwise.com/apps
  2. En la pagina de la app, generar una API key personal
  3. Guardarla en {path} como: SPLITWISE_API_KEY=<key>   (chmod 600)
  El skill nunca escribe ese archivo ni copia la key a ningun lado."""


class ApiError(Exception):
    pass


def die(msg, code=1):
    print(f"ERR>  {msg}")
    sys.exit(code)


# ----------------------------------------------------------------- credentials
def load_key():
    key = os.environ.get("SPLITWISE_API_KEY", "").strip()
    if not key and ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("SPLITWISE_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"').strip("'")
    if not key:
        ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
        print(BOOTSTRAP.format(path=ENV_PATH))
        sys.exit(2)
    return key


# ------------------------------------------------------------------------ http
def call(key, method, path, params=None, body=None, retry=True):
    url = f"{BASE}/{path}"
    if params:
        clean = {k: v for k, v in params.items() if v is not None}
        if clean:
            url += "?" + urllib.parse.urlencode(clean)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {key}")
    req.add_header("Accept", "application/json")
    # Cloudflare bans the default Python-urllib signature (error 1010) before auth runs
    req.add_header("User-Agent", "splitwise-skill/1.0")
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        text = e.read().decode("utf-8", errors="replace")
        if e.code == 429 and retry:
            time.sleep(3)
            return call(key, method, path, params, body, retry=False)
        raise ApiError(f"HTTP {e.code} {path}: {text[:600]}")
    except urllib.error.URLError as e:
        raise ApiError(f"red: {path}: {e.reason}")
    payload = json.loads(raw) if raw else {}
    errs = payload.get("errors")
    if errs:
        raise ApiError(f"{path}: {json.dumps(errs, ensure_ascii=False)}")
    if payload.get("success") is False:
        raise ApiError(f"{path}: success=false")
    if "error" in payload:
        raise ApiError(f"{path}: {payload['error']}")
    return payload


# --------------------------------------------------------------------- helpers
def norm(s):
    s = unicodedata.normalize("NFKD", s or "").encode("ascii", "ignore").decode()
    return " ".join(s.lower().split())


def money(text):
    t = str(text).strip().replace(" ", "")
    if "," in t and "." in t:
        t = t.replace(".", "").replace(",", ".")
    elif "," in t:
        t = t.replace(",", ".")
    try:
        d = Decimal(t)
    except InvalidOperation:
        die(f"importe invalido: {text}")
    if d < 0:
        die(f"importe negativo: {text}")
    if d != d.quantize(CENT):
        die(f"importe con mas de 2 decimales: {text}")
    return d.quantize(CENT)


def fmt(d):
    return f"{d:.2f}"


def fmt_es(d):
    return f"{d:.2f}".replace(".", ",")


def full_name(u):
    return " ".join(p for p in [u.get("first_name"), u.get("last_name")] if p)


def split_equal(cost, n):
    base = (cost / n).quantize(CENT, rounding=ROUND_DOWN)
    cents = int((cost - base * n) / CENT)
    return [base + (CENT if i < cents else Decimal("0")) for i in range(n)]


def split_percent(cost, pcts):
    if sum(pcts) != Decimal("100"):
        die(f"los porcentajes suman {sum(pcts)}, no 100")
    raw = [cost * p / Decimal("100") for p in pcts]
    parts = [r.quantize(CENT, rounding=ROUND_DOWN) for r in raw]
    cents = int((cost - sum(parts)) / CENT)
    order = sorted(range(len(raw)), key=lambda i: raw[i] - parts[i], reverse=True)
    for i in order[:cents]:
        parts[i] += CENT
    return parts


def parse_pairs(items, what, allow_bare=False):
    """['Juan=10,50', 'Ana'] -> [('Juan', Decimal|None)]"""
    out = []
    for item in items or []:
        if "=" in item:
            name, amount = item.split("=", 1)
            out.append((name.strip(), money(amount)))
        elif allow_bare:
            out.append((item.strip(), None))
        else:
            die(f"{what} necesita NOMBRE=MONTO: {item}")
    return out


def pick(candidates, query, label, render):
    """candidates: [(names, obj)]. Exact match, then substring; unique or exit 3."""
    q = norm(query)
    if q.isdigit():
        for _, obj in candidates:
            if str(obj.get("id")) == q:
                return obj
    exact = [obj for names, obj in candidates if any(norm(n) == q for n in names if n)]
    if len(exact) == 1:
        return exact[0]
    hits = exact or [obj for names, obj in candidates if any(q in norm(n) for n in names if n)]
    if len(hits) == 1:
        return hits[0]
    if not hits:
        print(f"ERR>  {label} '{query}' no encontrado. Opciones:")
        for _, obj in candidates:
            print("  " + render(obj))
    else:
        print(f"ERR>  {label} '{query}' ambiguo. Candidatos:")
        for obj in hits:
            print("  " + render(obj))
    sys.exit(3)


def render_group(g):
    return f"{g['id']:<10} {g['name']}  ({len(g.get('members', []))} miembros)"


def render_user(u):
    return f"{u['id']:<10} {full_name(u):<28} {u.get('email') or ''}"


def resolve_group(key, query):
    groups = call(key, "GET", "get_groups")["groups"]
    g = pick([([g["name"]], g) for g in groups], query, "grupo", render_group)
    return call(key, "GET", f"get_group/{g['id']}")["group"]


def resolve_member(group, me, query):
    if norm(query) in SELF_WORDS:
        for m in group["members"]:
            if m["id"] == me["id"]:
                return m
        die("el usuario actual no es miembro del grupo")
    cands = [
        ([m.get("first_name"), full_name(m), m.get("email"), m.get("last_name")], m)
        for m in group["members"]
    ]
    return pick(cands, query, "persona", render_user)


def iso_date(text):
    try:
        d = dt.date.fromisoformat(text)
    except ValueError:
        die(f"fecha invalida (AAAA-MM-DD): {text}")
    return d


def print_table(rows, headers):
    widths = [len(h) for h in headers]
    for r in rows:
        for i, c in enumerate(r):
            widths[i] = max(widths[i], len(str(c)))
    print("  " + "  ".join(f"{h:<{w}}" for h, w in zip(headers, widths)))
    for r in rows:
        print("  " + "  ".join(f"{str(c):<{w}}" for c, w in zip(r, widths)))


# -------------------------------------------------------------------- commands
def cmd_me(a, key):
    me = call(key, "GET", "get_current_user")["user"]
    if a.json:
        print(json.dumps(me, ensure_ascii=False, indent=2))
        return
    print(f"OK>   {full_name(me)} (id {me['id']}) {me.get('email')}  moneda {me.get('default_currency')}")


def cmd_groups(a, key):
    groups = call(key, "GET", "get_groups")["groups"]
    if a.json:
        print(json.dumps(groups, ensure_ascii=False, indent=2))
        return
    print_table(
        [(g["id"], g["name"], g.get("group_type") or "", len(g.get("members", []))) for g in groups],
        ["ID", "GRUPO", "TIPO", "MIEMBROS"],
    )


def cmd_members(a, key):
    group = resolve_group(key, a.group)
    if a.json:
        print(json.dumps(group["members"], ensure_ascii=False, indent=2))
        return
    print(f"OK>   grupo {group['name']} (id {group['id']})")
    rows = []
    for m in group["members"]:
        bal = " ".join(f"{b['currency_code']} {fmt_es(Decimal(b['amount']))}" for b in m.get("balance", [])) or "0"
        rows.append((m["id"], full_name(m), m.get("email") or "", bal))
    print_table(rows, ["ID", "NOMBRE", "EMAIL", "BALANCE"])


def cmd_friends(a, key):
    friends = call(key, "GET", "get_friends")["friends"]
    if a.json:
        print(json.dumps(friends, ensure_ascii=False, indent=2))
        return
    rows = []
    for f in friends:
        bal = " ".join(f"{b['currency_code']} {fmt_es(Decimal(b['amount']))}" for b in f.get("balance", [])) or "0"
        rows.append((f["id"], full_name(f), f.get("email") or "", bal))
    print_table(rows, ["ID", "NOMBRE", "EMAIL", "BALANCE"])


def cmd_categories(a, key):
    cats = call(key, "GET", "get_categories")["categories"]
    if a.json:
        print(json.dumps(cats, ensure_ascii=False, indent=2))
        return
    rows = []
    for c in cats:
        for s in c.get("subcategories", []):
            rows.append((s["id"], c["name"], s["name"]))
    print_table(rows, ["ID", "CATEGORIA", "SUBCATEGORIA (usar este id)"])


def fetch_expenses(key, group_id, since=None, until=None, limit=100):
    params = {"group_id": group_id, "limit": limit}
    if since:
        params["dated_after"] = f"{since}T00:00:00Z"
    if until:
        params["dated_before"] = f"{until}T23:59:59Z"
    return call(key, "GET", "get_expenses", params=params)["expenses"]


def payer_names(e):
    return ", ".join(full_name(u["user"]) for u in e.get("users", []) if Decimal(u["paid_share"]) > 0)


def cmd_expenses(a, key):
    group = resolve_group(key, a.group)
    exps = fetch_expenses(key, group["id"], a.since, a.until, a.limit)
    live = [e for e in exps if not e.get("deleted_at")] if not a.all else exps
    if a.json:
        print(json.dumps(live, ensure_ascii=False, indent=2))
        return
    print(f"OK>   grupo {group['name']} (id {group['id']}) — {len(live)} gastos")
    rows = [
        (e["id"], e["date"][:10], f"{e['currency_code']} {fmt_es(Decimal(e['cost']))}", e["description"],
         payer_names(e), "borrado" if e.get("deleted_at") else "")
        for e in live
    ]
    print_table(rows, ["ID", "FECHA", "IMPORTE", "DESCRIPCION", "PAGO", ""])


def show_balances(group):
    by_id = {m["id"]: full_name(m) for m in group["members"]}
    rows = []
    for m in group["members"]:
        bal = " ".join(f"{b['currency_code']} {fmt_es(Decimal(b['amount']))}" for b in m.get("balance", [])) or "0"
        rows.append((full_name(m), bal))
    print_table(rows, ["MIEMBRO", "BALANCE"])
    debts = group.get("simplified_debts") or []
    if debts:
        print("  DEUDAS SIMPLIFICADAS")
        for d in debts:
            print(f"  {by_id.get(d['from'], d['from'])} -> {by_id.get(d['to'], d['to'])}: "
                  f"{d['currency_code']} {fmt_es(Decimal(d['amount']))}")


def cmd_balances(a, key):
    group = resolve_group(key, a.group)
    if a.json:
        print(json.dumps({"members": group["members"], "simplified_debts": group.get("simplified_debts")},
                         ensure_ascii=False, indent=2))
        return
    print(f"OK>   grupo {group['name']} (id {group['id']})")
    show_balances(group)


def build_shares(a, cost, group, me):
    """Returns ordered list of (member, paid, owed) after resolution and validation."""
    paid_spec = parse_pairs(a.paid, "--paid", allow_bare=True)
    if not paid_spec:
        paid_spec = [("yo", None)]
    if len(paid_spec) == 1 and paid_spec[0][1] is None:
        paid_spec = [(paid_spec[0][0], cost)]
    if any(amt is None for _, amt in paid_spec):
        die("con varios pagadores cada --paid necesita NOMBRE=MONTO")
    if sum(amt for _, amt in paid_spec) != cost:
        die(f"los pagos suman {fmt_es(sum(amt for _, amt in paid_spec))} y el costo es {fmt_es(cost)}")

    if a.shares:
        owed_spec = parse_pairs(a.shares, "--share")
        if sum(amt for _, amt in owed_spec) != cost:
            die(f"las deudas suman {fmt_es(sum(amt for _, amt in owed_spec))} y el costo es {fmt_es(cost)}")
    elif a.percent:
        pct = parse_pairs(a.percent, "--percent")
        parts = split_percent(cost, [p for _, p in pct])
        owed_spec = [(name, amt) for (name, _), amt in zip(pct, parts)]
    else:
        names = a.among or [full_name(m) for m in group["members"]]
        parts = split_equal(cost, len(names))
        owed_spec = list(zip(names, parts))

    table = {}
    order = []
    for name, amt in paid_spec:
        m = resolve_member(group, me, name)
        row = table.setdefault(m["id"], {"member": m, "paid": Decimal("0"), "owed": Decimal("0")})
        row["paid"] += amt
        if m["id"] not in order:
            order.append(m["id"])
    for name, amt in owed_spec:
        m = resolve_member(group, me, name)
        row = table.setdefault(m["id"], {"member": m, "paid": Decimal("0"), "owed": Decimal("0")})
        row["owed"] += amt
        if m["id"] not in order:
            order.append(m["id"])
    rows = [(table[i]["member"], table[i]["paid"], table[i]["owed"]) for i in order]
    if sum(r[1] for r in rows) != cost or sum(r[2] for r in rows) != cost:
        die("la suma de pagos o deudas no coincide con el costo tras la resolucion")
    return rows


def preview(group, desc, cost, currency, date, rows, category=None, details=None):
    print(f"  GRUPO        {group['name']} (id {group['id']})")
    print(f"  DESCRIPCION  {desc}")
    print(f"  IMPORTE      {currency} {fmt_es(cost)}   FECHA {date}")
    if category:
        print(f"  CATEGORIA    {category}")
    if details:
        print(f"  DETALLE      {details}")
    print_table(
        [(full_name(m), fmt_es(p), fmt_es(o), fmt_es(p - o)) for m, p, o in rows],
        ["PERSONA", "PAGO", "DEBE", "NETO"],
    )


def shares_payload(rows):
    body = {}
    for i, (m, paid, owed) in enumerate(rows):
        body[f"users__{i}__user_id"] = m["id"]
        body[f"users__{i}__paid_share"] = fmt(paid)
        body[f"users__{i}__owed_share"] = fmt(owed)
    return body


def find_duplicates(key, group_id, date, cost, desc):
    day = iso_date(date)
    exps = fetch_expenses(key, group_id, str(day - dt.timedelta(days=1)), str(day + dt.timedelta(days=1)))
    return [
        e for e in exps
        if not e.get("deleted_at") and Decimal(e["cost"]) == cost and norm(e["description"]) == norm(desc)
    ]


def local_checks(a):
    """Arithmetic and format validation that needs no credential or network."""
    cost = money(a.cost)
    if a.shares:
        owed = sum(amt for _, amt in parse_pairs(a.shares, "--share"))
        if owed != cost:
            die(f"las deudas suman {fmt_es(owed)} y el costo es {fmt_es(cost)}")
    if a.percent:
        split_percent(cost, [p for _, p in parse_pairs(a.percent, "--percent")])
    paid_spec = parse_pairs(a.paid, "--paid", allow_bare=True)
    if len(paid_spec) > 1:
        if any(amt is None for _, amt in paid_spec):
            die("con varios pagadores cada --paid necesita NOMBRE=MONTO")
        paid = sum(amt for _, amt in paid_spec)
        if paid != cost:
            die(f"los pagos suman {fmt_es(paid)} y el costo es {fmt_es(cost)}")
    date = str(iso_date(a.date)) if a.date else str(dt.date.today())
    return cost, date


def cmd_add(a, key):
    cost, date = local_checks(a)

    me = call(key, "GET", "get_current_user")["user"]
    group = resolve_group(key, a.group)
    currency = (a.currency or me.get("default_currency") or "USD").upper()
    rows = build_shares(a, cost, group, me)

    dups = find_duplicates(key, group["id"], date, cost, a.desc)
    preview(group, a.desc, cost, currency, date, rows, a.category, a.details)
    for d in dups:
        print(f"WARN> posible duplicado: id {d['id']} {d['date'][:10]} {d['description']} "
              f"{d['currency_code']} {fmt_es(Decimal(d['cost']))}")

    body = {
        "cost": fmt(cost),
        "description": a.desc,
        "date": f"{date}T12:00:00Z",
        "currency_code": currency,
        "group_id": group["id"],
    }
    if a.category:
        body["category_id"] = int(a.category)
    if a.details:
        body["details"] = a.details
    body.update(shares_payload(rows))

    if a.dry_run:
        print("DRY>  payload create_expense:")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return
    if dups and not a.force:
        die("duplicado detectado; repetir con --force para cargarlo igual", 5)
    out = call(key, "POST", "create_expense", body=body)
    exp = out["expenses"][0]
    print(f"OK>   gasto creado id {exp['id']} — {exp['currency_code']} {fmt_es(Decimal(exp['cost']))} {exp['description']}")
    show_balances(call(key, "GET", f"get_group/{group['id']}")["group"])


def cmd_update(a, key):
    me = call(key, "GET", "get_current_user")["user"]
    exp = call(key, "GET", f"get_expense/{a.id}")["expense"]
    if exp.get("deleted_at"):
        die(f"el gasto {a.id} esta borrado")
    group = call(key, "GET", f"get_group/{exp['group_id'] or 0}")["group"] if exp.get("group_id") else None
    cost = money(a.cost) if a.cost else Decimal(exp["cost"]).quantize(CENT)
    body = {}
    if a.desc:
        body["description"] = a.desc
    if a.cost:
        body["cost"] = fmt(cost)
    if a.date:
        body["date"] = f"{iso_date(a.date)}T12:00:00Z"
    if a.currency:
        body["currency_code"] = a.currency.upper()
    if a.category:
        body["category_id"] = int(a.category)
    if a.details:
        body["details"] = a.details
    rows = None
    if a.paid or a.shares or a.percent or a.among or a.cost:
        if group is None:
            die("reparto solo soportado para gastos dentro de un grupo")
        rows = build_shares(a, cost, group, me)
        body.update(shares_payload(rows))
    if not body:
        die("nada que actualizar")
    print(f"  GASTO {exp['id']}: {exp['description']} {exp['currency_code']} {fmt_es(Decimal(exp['cost']))} ({exp['date'][:10]})")
    if rows:
        preview(group, a.desc or exp["description"], cost, a.currency or exp["currency_code"],
                a.date or exp["date"][:10], rows, a.category, a.details)
    if a.dry_run:
        print("DRY>  payload update_expense:")
        print(json.dumps(body, ensure_ascii=False, indent=2))
        return
    out = call(key, "POST", f"update_expense/{a.id}", body=body)
    e = out["expenses"][0]
    print(f"OK>   gasto actualizado id {e['id']} — {e['currency_code']} {fmt_es(Decimal(e['cost']))} {e['description']}")
    if group:
        show_balances(call(key, "GET", f"get_group/{group['id']}")["group"])


def cmd_delete(a, key):
    exp = call(key, "GET", f"get_expense/{a.id}")["expense"]
    print(f"  GASTO {exp['id']}: {exp['description']} {exp['currency_code']} {fmt_es(Decimal(exp['cost']))} "
          f"({exp['date'][:10]}) pago {payer_names(exp)}")
    if exp.get("deleted_at"):
        die("ya estaba borrado")
    if a.dry_run:
        print("DRY>  no se borra")
        return
    call(key, "POST", f"delete_expense/{a.id}", body={})
    print(f"OK>   gasto {a.id} borrado (undelete_expense/{a.id} lo recupera)")


def cmd_comment(a, key):
    out = call(key, "POST", "create_comment", body={"expense_id": int(a.id), "content": a.text})
    c = out["comment"]
    print(f"OK>   comentario {c['id']} en gasto {a.id}")


def cmd_selftest(a, key=None):
    checks = [
        ("money comma", money("11,30") == Decimal("11.30")),
        ("money thousands", money("1.234,50") == Decimal("1234.50")),
        ("equal 100/3", split_equal(Decimal("100.00"), 3) == [Decimal("33.34"), Decimal("33.33"), Decimal("33.33")]),
        ("equal sum", sum(split_equal(Decimal("14.20"), 4)) == Decimal("14.20")),
        ("percent 60/40", split_percent(Decimal("25.00"), [Decimal("60"), Decimal("40")]) == [Decimal("15.00"), Decimal("10.00")]),
        ("percent sum", sum(split_percent(Decimal("10.01"), [Decimal("33.3"), Decimal("33.3"), Decimal("33.4")])) == Decimal("10.01")),
        ("norm accents", norm("José  Pérez") == "jose perez"),
    ]
    bad = 0
    for name, ok in checks:
        print(f"  {'OK' if ok else 'ERR'}  {name}")
        bad += 0 if ok else 1
    sys.exit(1 if bad else 0)


# ------------------------------------------------------------------------ main
def add_share_args(p):
    p.add_argument("--paid", action="append", help="NOMBRE o NOMBRE=MONTO (repetible); default: yo pago todo")
    p.add_argument("--share", dest="shares", action="append", help="NOMBRE=MONTO exacto (repetible)")
    p.add_argument("--percent", action="append", help="NOMBRE=PCT (repetible, suma 100)")
    p.add_argument("--among", action="append", help="NOMBRE para reparto igual (repetible); default: todo el grupo")
    p.add_argument("--equal", action="store_true", help="reparto en partes iguales (default cuando no hay --share/--percent)")


def main():
    ap = argparse.ArgumentParser(description="Splitwise CLI (splitwise skill)")
    ap.add_argument("--json", action="store_true", help="salida JSON cruda en lecturas")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("me")
    sub.add_parser("groups")
    p = sub.add_parser("members"); p.add_argument("group")
    sub.add_parser("friends")
    sub.add_parser("categories")
    p = sub.add_parser("expenses"); p.add_argument("group")
    p.add_argument("--since"); p.add_argument("--until"); p.add_argument("--limit", type=int, default=100)
    p.add_argument("--all", action="store_true", help="incluir borrados")
    p = sub.add_parser("balances"); p.add_argument("group")

    p = sub.add_parser("add-expense")
    p.add_argument("--group", required=True); p.add_argument("--desc", required=True)
    p.add_argument("--cost", required=True); p.add_argument("--currency"); p.add_argument("--date")
    p.add_argument("--category", help="subcategory id de get_categories"); p.add_argument("--details")
    add_share_args(p)
    p.add_argument("--dry-run", action="store_true"); p.add_argument("--force", action="store_true")

    p = sub.add_parser("update-expense"); p.add_argument("id")
    p.add_argument("--desc"); p.add_argument("--cost"); p.add_argument("--currency"); p.add_argument("--date")
    p.add_argument("--category"); p.add_argument("--details")
    add_share_args(p)
    p.add_argument("--dry-run", action="store_true")

    p = sub.add_parser("delete-expense"); p.add_argument("id"); p.add_argument("--dry-run", action="store_true")
    p = sub.add_parser("comment"); p.add_argument("id"); p.add_argument("text")
    sub.add_parser("selftest")

    a = ap.parse_args()
    if a.cmd == "selftest":
        cmd_selftest(a)
    handlers = {
        "me": cmd_me, "groups": cmd_groups, "members": cmd_members, "friends": cmd_friends,
        "categories": cmd_categories,
        "expenses": cmd_expenses, "balances": cmd_balances, "add-expense": cmd_add,
        "update-expense": cmd_update, "delete-expense": cmd_delete, "comment": cmd_comment,
    }
    if a.cmd == "add-expense":
        local_checks(a)  # rejects bad arithmetic before any credential or network use
    key = load_key()
    try:
        handlers[a.cmd](a, key)
    except ApiError as e:
        die(str(e))


if __name__ == "__main__":
    main()

"""Seed an open-board scenario so the broker's suggest_matches optimizer has a
compelling, recognizable set of pairings to recommend.

Creates open SELL listings and open BUY requests using the seeded U.S. Swine
Leaders clients, with realistic spreads across WEAN_PIGS and FEEDER_PIGS so the
profit-maximizing assignment is non-trivial and headlined by names like
Iowa Select Farms -> JBS USA, Pipestone -> Tyson, etc.

Open-order shape (mirrors how the live board stores them):
  - SELL: seller_id set, buyer_id NULL, in-price on buy_price, sell_price NULL
  - BUY : buyer_id set, seller_id NULL, out-price on sell_price, buy_price NULL
  - status='PARTIAL', trade_type='DELIVERED', health='CLEAN'

Idempotent: removes its own prior rows (additional_terms tag) before inserting.
"""
from __future__ import annotations
import uuid
from datetime import datetime
import pg8000.native

TAG = "DEMO_OPT_SCENARIO"

con = pg8000.native.Connection(user="postgres", password="password",
                               host="127.0.0.1", port=5432, database="livestock")
con.run("set search_path to pigproject, public")

def run(sql, **kw):
    return con.run(sql, **kw)

# Map swine-leader company name -> user id
ids = {name: uid for (uid, name) in
       run("""select u.id, c.name from users u join companies c on u.company_id=c.id
              where u.email like '%@swineleads.demo'""")}

def uid(name):
    if name not in ids:
        raise SystemExit(f"Missing seeded client '{name}' — run seed_swine_leaders.py first.")
    return ids[name]

# (company, market, in_price, head) — ELM pays the seller in_price/head
SELLS = [
    ("Iowa Select Farms", "WEAN_PIGS",   44, 2200),
    ("Carthage System",   "WEAN_PIGS",   45, 1800),
    ("Christensen Farms", "WEAN_PIGS",   46, 1500),
    ("The Maschhoffs",    "FEEDER_PIGS", 58, 2000),
    ("Pipestone Management", "FEEDER_PIGS", 60, 2500),
]
# (company, market, out_price, head) — buyer pays ELM out_price/head
BUYS = [
    ("JBS USA",          "WEAN_PIGS",   57, 2200),
    ("Seaboard Foods",   "WEAN_PIGS",   55, 1800),
    ("AMVC Management",  "WEAN_PIGS",   54, 1500),
    ("Tyson Foods",      "FEEDER_PIGS", 70, 2000),
    ("Prestage Farms",   "FEEDER_PIGS", 66, 2500),
]

# --- clean up prior scenario ---
removed = run(f"delete from orders where additional_terms = '{TAG}'")
print("Cleaned prior scenario rows.")

# resync the id sequence above the live max (dev rows loaded out-of-band)
mx = run("select coalesce(max(id),0) from orders")[0][0]
run(f"select setval('orders_id_generator', {mx})")

now = datetime.now()
def short_id(seed):
    return str(abs(hash(seed)) % 900000 + 100000)

created = []
for (co, market, in_price, head) in SELLS:
    sid = short_id("S" + co)
    run("""insert into orders (guid, trade_type, market, health, quantity, status,
                               buy_price, seller_id, short_id, additional_terms,
                               created_date_time, updated_at, deleted)
           values (:g,'DELIVERED',:m,'CLEAN',:q,'PARTIAL',:p,:s,:sid,:tag,:c,:c,false)""",
        g=uuid.uuid4(), m=market, q=head, p=in_price, s=uid(co), sid=sid, tag=TAG, c=now)
    created.append(("SELL", co, market, in_price, head, sid))

for (co, market, out_price, head) in BUYS:
    sid = short_id("B" + co)
    run("""insert into orders (guid, trade_type, market, health, quantity, status,
                               sell_price, buyer_id, short_id, additional_terms,
                               created_date_time, updated_at, deleted)
           values (:g,'DELIVERED',:m,'CLEAN',:q,'PARTIAL',:p,:b,:sid,:tag,:c,:c,false)""",
        g=uuid.uuid4(), m=market, q=head, p=out_price, b=uid(co), sid=sid, tag=TAG, c=now)
    created.append(("BUY", co, market, out_price, head, sid))

print(f"\nSeeded {len(created)} open orders:\n")
print(f"  {'Side':4} {'Company':22} {'Market':12} {'Price':>6} {'Head':>6}  {'OrderID'}")
for side, co, market, price, head, sid in created:
    print(f"  {side:4} {co:22} {market:12} ${price:>5} {head:>6}  {sid}")

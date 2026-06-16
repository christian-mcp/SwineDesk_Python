"""Seed SwineDesk (livestock DB) with real swine producers from the U.S. Swine Leaders 2024 list.

Creates one Company + one PRODUCER User per client (buyer/seller), with HQ state so the
CRM "filter by state" + broadcast filters work, plus high-value Notes on the top accounts.

Idempotent: removes any prior seed (identified by the @swineleads.demo email domain) first.
"""
from __future__ import annotations
import uuid
from datetime import datetime
import pg8000.native

DOMAIN = "swineleads.demo"
ADMIN_EMAIL = "local.admin.2@example.com"  # the broker/admin who "owns" the notes

con = pg8000.native.Connection(user="postgres", password="password",
                               host="127.0.0.1", port=5432, database="livestock")
con.run("set search_path to pigproject, public")

def run(sql, **kw):
    return con.run(sql, **kw)

# (company_name, contact_first, contact_last, hq_city, state, area_code, last4,
#  producer_type, sows, high_value, note)
CLIENTS = [
    # ---- SELLERS (sow operations selling wean/feeder pigs) ----
    ("Smithfield Foods",       "Dan",    "Whitley",   "Smithfield",      "VA", "757", "0101", "SELLER", 600000, True,
     "HIGH-VALUE SELLER. Largest U.S. pork producer — 600,000 sows (WH Group spin-off). Priority relationship."),
    ("Pipestone Management",   "Barry",  "Kerkaert",  "Pipestone",       "MN", "507", "0102", "SELLER", 392000, True,
     "HIGH-VALUE SELLER. Vet-led multi-owner managed system, 392,000 sows, wide U.S. presence."),
    ("Iowa Select Farms",      "Jeff",   "Hansen",    "Iowa Falls",      "IA", "641", "0103", "SELLER", 260000, True,
     "HIGH-VALUE SELLER. Largest Iowa producer — 260,000 sows, strong local roots."),
    ("Carthage System",        "Joel",   "Webb",      "Carthage",        "IL", "217", "0104", "SELLER", 189200, False,
     None),
    ("Christensen Farms",      "Glenn",  "Stolt",     "Sleepy Eye",      "MN", "507", "0105", "SELLER", 140000, False,
     None),
    ("The Maschhoffs",         "Ken",    "Maschhoff", "Carlyle",         "IL", "618", "0106", "SELLER", 120000, False,
     None),
    ("Pillen Family Farms",    "Jim",    "Pillen",    "Columbus",        "NE", "402", "0107", "SELLER",  78000, False,
     None),
    ("New Fashion Pork",       "Brad",   "Freking",   "Jackson",         "MN", "507", "0108", "SELLER",  57000, False,
     None),

    # ---- BUYERS (integrators / finishers buying pigs) ----
    ("Seaboard Foods",         "Terry",  "Holton",    "Shawnee Mission", "KS", "913", "0109", "BUYER",  336000, True,
     "HIGH-VALUE BUYER. Vertically integrated processor, 336,000 sows. Large, reliable demand."),
    ("JBS USA",                "Andre",  "Nogueira",  "Greeley",         "CO", "970", "0110", "BUYER",  259320, True,
     "HIGH-VALUE BUYER. JBS USA pork division — 259,000 sows, Greeley CO. Priority account."),
    ("Prestage Farms",         "John",   "Prestage",  "Clinton",         "NC", "910", "0111", "BUYER",  170000, True,
     "HIGH-VALUE BUYER. Multi-state NC integrator, 170,000 sows."),
    ("AMVC Management",        "Daryl",  "Olsen",     "Audubon",         "IA", "712", "0112", "BUYER",  161500, True,
     "HIGH-VALUE BUYER. Iowa vet-services group managing a large sow base, 161,500 sows."),
    ("Tyson Foods",            "Donnie", "King",      "Springdale",      "AR", "479", "0113", "BUYER",   72000, True,
     "HIGH-VALUE BUYER. One of the largest U.S. meat processors, diversified protein demand."),
    ("Eichelberger Farms",     "Brian",  "Eichel",    "Wayland",         "IA", "319", "0114", "BUYER",   66500, False,
     None),
    ("Reicks View Farms",      "Rich",   "Reicks",    "Lawler",          "IA", "563", "0115", "BUYER",   64000, False,
     None),
    ("Brenneman Pork",         "Rob",    "Brenneman", "Washington",      "IA", "319", "0116", "BUYER",   52000, False,
     None),
]

def slug(name: str) -> str:
    return "".join(ch for ch in name.lower() if ch.isalnum())[:18]

# --- clean up prior seed (cascade: notes -> users -> companies) ---
old_user_guids = [r[0] for r in run(
    f"select guid from users where email like '%@{DOMAIN}'")]
for g in old_user_guids:
    run("delete from notes where linked_user_id = :g", g=g)
run(f"delete from users where email like '%@{DOMAIN}'")
run(f"delete from companies where email like '%@{DOMAIN}'")
print(f"Cleaned up {len(old_user_guids)} previously-seeded users.")

# sequences are behind the live max id (dev rows were loaded out-of-band) — resync
for tbl, seq in [("companies","companies_id_generator"),
                 ("users","users_id_generator"),
                 ("notes","notes_id_generator")]:
    mx = run(f"select coalesce(max(id),0) from {tbl}")[0][0]
    run(f"select setval('{seq}', {mx})")
    print(f"  resynced {seq} -> next id {mx+1}")

now = datetime.now()
created = []
for (coname, first, last, city, state, ac, last4, ptype, sows, hv, note) in CLIENTS:
    s = slug(coname)
    co_guid = uuid.uuid4()
    co_email = f"{s}@{DOMAIN}"
    phone = f"+1{ac}555{last4}"
    co_id = run(
        """insert into companies (guid, name, email, phone, city, state_code,
                                  created_date_time, updated_at, deleted)
           values (:guid,:name,:email,:phone,:city,:state,:c,:u,false)
           returning id""",
        guid=co_guid, name=coname, email=co_email, phone=phone, city=city,
        state=state, c=now, u=now)[0][0]

    u_guid = uuid.uuid4()
    u_email = f"{s}.contact@{DOMAIN}"
    short_id = str(abs(hash(s)) % 900000 + 100000)
    run(
        """insert into users (guid, first_name, last_name, email, phone, role,
                              company_id, short_id, producer_type,
                              created_date_time, updated_at, deleted)
           values (:guid,:fn,:ln,:email,:phone,'PRODUCER',:co,:sid,:pt,:c,:u,false)""",
        guid=u_guid, fn=first, ln=last, email=u_email, phone=phone,
        co=co_id, sid=short_id, pt=ptype, c=now, u=now)

    if note:
        run(
            """insert into notes (guid, body, created_by_email, created_at, linked_user_id)
               values (:guid,:body,:cb,:ca,:lu)""",
            guid=uuid.uuid4(), body=note, cb=ADMIN_EMAIL, ca=now, lu=u_guid)

    created.append((coname, state, ptype, sows, hv))

print(f"\nSeeded {len(created)} producers:\n")
print(f"  {'Company':24} {'ST':3} {'Type':7} {'Sows':>8}  HighValue")
for coname, state, ptype, sows, hv in created:
    print(f"  {coname:24} {state:3} {ptype:7} {sows:>8,}  {'★' if hv else ''}")

# quick segment summaries straight from the DB (mirrors the CRM filters)
print("\n--- segment checks (mirrors find_contacts filters) ---")
iowa_buyers = run("""select c.name from users u join companies c on u.company_id=c.id
                     where u.producer_type='BUYER' and c.state_code='IA'
                     and u.email like '%@{}' order by c.name""".format(DOMAIN))
print("Iowa buyers:", ", ".join(r[0] for r in iowa_buyers))
hv = run(f"""select c.name from users u join companies c on u.company_id=c.id
             join notes n on n.linked_user_id=u.guid
             where n.body like 'HIGH-VALUE%' and u.email like '%@{DOMAIN}'
             order by u.producer_type, c.name""")
print("High-value (noted):", ", ".join(r[0] for r in hv))

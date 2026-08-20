"""PHASE 0 SPIKE: does Lightspeed accept a write to SpecialOrder.orderLineID?

Allocating a special order to a purchase order means setting `SpecialOrder.orderLineID` to an
OrderLine on that PO. Reading it is confirmed; WRITING it is the single unverified fact the
whole allocation feature rests on, so this probe answers it before any of it is built.

QUARANTINED, like the other live-mutation scripts in this repo. It refuses to run without:
  * the full triple write gate open (LIGHTSPEED_WRITES_ENABLED + _WRITE_APPROVAL_TOKEN +
    _WRITE_SHOP_ALLOWLIST), and
  * an explicit --commit flag.
With either missing it reports what it *would* do and exits.

Two stages, safest first:

  STAGE 1 - ZERO RISK. Take a special order that already points at an OrderLine and PUT the
  SAME value back. Success proves the endpoint exists and confirms the payload shape; failure
  proves the write path does not exist. Either way nothing changes semantically, even if the
  write succeeds. Several payload shapes are tried because the legacy API's convention is not
  documented for this resource.

  STAGE 2 - REVERSIBLE. Take an UNALLOCATED special order (orderLineID == 0), point it at a
  real OrderLine, re-GET to confirm the value actually persisted, then restore it to 0 and
  re-GET again to confirm the restore. Runs only if stage 1 passes. The exposure window is one
  currently-unallocated SO briefly reading as allocated.
"""

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv(dotenv_path="/Users/alesfraser-maraun/Desktop/BICI_replen_level_automation/backend/.env")
sys.path.insert(0, ".")

from app.services.lightspeed_client import LightspeedClient, lightspeed_writes_enabled  # noqa: E402

COMMIT = "--commit" in sys.argv


def _put(client, so_id, payload, as_form=False):
    url = f"{client.legacy_base_url}/SpecialOrder/{so_id}.json"
    headers = client._get_headers()
    headers["Accept"] = "application/json"
    if as_form:
        return requests.put(url, headers=headers, data=payload, timeout=20)
    return requests.put(url, headers=headers, json=payload, timeout=20)


def _get_special_order(client, so_id):
    r = client._legacy_request("GET", "/SpecialOrder.json", params={"specialOrderID": str(so_id)})
    if r is None or r.status_code != 200:
        return None
    rows = r.json().get("SpecialOrder")
    if isinstance(rows, dict):
        return rows
    return (rows or [None])[0]


def main():
    client = LightspeedClient()
    sos = client.get_special_orders()

    # Probe a CURRENT record at a real trading store. Shop 1 and other legacy/virtual shops
    # carry decade-old rows whose state nobody understands, which is the wrong thing to be
    # poking even with a same-value write. Newest first, so the target is a live SO the team
    # would recognise if anything did go wrong.
    real_shops = {"2", "3", "20"}
    allow = {v.strip() for v in os.getenv("LIGHTSPEED_WRITE_SHOP_ALLOWLIST", "").split(",") if v.strip()}
    preferred = allow or real_shops

    def _recency(s):
        return (s.get("SaleLine") or {}).get("createTime") or s.get("timeStamp") or ""

    def _pick(rows):
        here = [r for r in rows if str(r.get("shopID")) in preferred]
        return sorted(here or rows, key=_recency, reverse=True)

    allocated = _pick([s for s in sos if str(s.get("orderLineID") or "0") not in ("0", "", "None")])
    unallocated = _pick([s for s in sos if str(s.get("orderLineID") or "0") in ("0", "", "None")])
    print(f"open special orders: {len(sos)}  allocated: {len(allocated)}  unallocated: {len(unallocated)}")
    print(f"preferring shops: {sorted(preferred)}")
    if not allocated:
        print("FAIL: no allocated special order available to probe with.")
        return 1

    target = allocated[0]
    so_id, current_line = target["specialOrderID"], str(target["orderLineID"])
    shop_id = str(target.get("shopID"))
    print(f"\nSTAGE 1 target: SO {so_id} (shop {shop_id}), orderLineID currently {current_line}")
    print("             writing the SAME value back -- no semantic change even on success.")

    gate_open = lightspeed_writes_enabled(shop_id)
    print(f"\nwrite gate for shop {shop_id}: {'OPEN' if gate_open else 'CLOSED'}   --commit: {COMMIT}")
    if not (gate_open and COMMIT):
        print("\nDRY RUN -- nothing sent. To actually probe:")
        print("  1. In backend/.env set:")
        print("       LIGHTSPEED_WRITES_ENABLED=true")
        print("       LIGHTSPEED_WRITE_APPROVAL_TOKEN=<any non-empty string>")
        print(f"       LIGHTSPEED_WRITE_SHOP_ALLOWLIST={shop_id}")
        print("  2. python3 test_so_allocation_spike.py --commit")
        return 0

    shapes = [
        ("flat json",    {"orderLineID": current_line}, False),
        ("wrapped json", {"SpecialOrder": {"orderLineID": current_line}}, False),
        ("form encoded", {"orderLineID": current_line}, True),
    ]
    winner = None
    for label, payload, as_form in shapes:
        try:
            resp = _put(client, so_id, payload, as_form=as_form)
        except Exception as e:
            print(f"  {label:<13} transport error: {e}")
            continue
        body = (resp.text or "")[:180].replace("\n", " ")
        print(f"  {label:<13} HTTP {resp.status_code}  {body}")
        if resp.status_code in (200, 201) and winner is None:
            winner = (label, as_form)
    if not winner:
        print("\nRESULT: Lightspeed does NOT accept a write to SpecialOrder.orderLineID.")
        print("        => Phase 4 ships recommend-only. Everything else is unaffected.")
        return 0

    print(f"\nSTAGE 1 PASS -- accepted payload shape: {winner[0]}")
    if not unallocated:
        print("STAGE 2 skipped: no unallocated special order to test a real change against.")
        return 0

    probe = unallocated[0]
    probe_id = probe["specialOrderID"]
    probe_shop = str(probe.get("shopID"))
    if not lightspeed_writes_enabled(probe_shop):
        print(f"STAGE 2 skipped: shop {probe_shop} is not in the write allowlist.")
        return 0

    print(f"\nSTAGE 2 target: SO {probe_id} (shop {probe_shop}), currently UNALLOCATED")
    print(f"             will set orderLineID -> {current_line}, verify, then restore to 0")
    label, as_form = winner

    def _send(value):
        body = {"SpecialOrder": {"orderLineID": value}} if label == "wrapped json" else {"orderLineID": value}
        return _put(client, probe_id, body, as_form=as_form)

    r1 = _send(current_line)
    after = _get_special_order(client, probe_id)
    got = str((after or {}).get("orderLineID") or "0")
    print(f"  set     -> HTTP {r1.status_code}; re-GET orderLineID = {got}  "
          f"({'PERSISTED' if got == current_line else 'did NOT persist'})")

    r2 = _send("0")
    restored = _get_special_order(client, probe_id)
    back = str((restored or {}).get("orderLineID") or "0")
    print(f"  restore -> HTTP {r2.status_code}; re-GET orderLineID = {back}  "
          f"({'RESTORED' if back in ('0', '', 'None') else '*** MANUAL FIX NEEDED ***'})")

    if got == current_line:
        print("\nRESULT: allocation write-back is VIABLE. Phase 4 can proceed.")
    else:
        print("\nRESULT: the PUT is accepted but the value does not persist.")
        print("        => treat as unsupported; ship recommend-only.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

# -*- coding: utf-8 -*-
"""
צינור ייבוא מחירים אמיתיים — BLATNER
=====================================
מושך את קבצי שקיפות המחירים הרשמיים (חוק המזון) של ויקטורי ויוחננוף,
ומעדכן את טבלת products ב-Supabase עם המחירים האמיתיים והמבצעים.

רץ אוטומטית כל יום דרך GitHub Actions (ראה .github/workflows/prices.yml).
דורש שני משתני סביבה (מוגדרים כ-GitHub Secrets):
  SUPABASE_URL         — כתובת הפרויקט
  SUPABASE_SERVICE_KEY — מפתח service_role (סודי!)

הרצה מקומית לבדיקה:
  pip install il-supermarket-scarper requests
  SUPABASE_URL=... SUPABASE_SERVICE_KEY=... python fetch_prices.py
"""
import os
import re
import gzip
import glob
import json
import xml.etree.ElementTree as ET

import requests

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

CHAINS = {
    "victory":   {"scraper": "VICTORY",   "store_hint": "צמח",   "col": "victory"},
    "yohananof": {"scraper": "YOHANANOF", "store_hint": "אדיסון", "col": "yohananof"},
}

DEPT_RULES = [
    (r"ירק|פרי|פירות|עשבי", "ירקות ופירות"),
    (r"חלב|גבינ|יוגורט|ביצים|מעדן|שמנת|חמאה|קוטג", "מוצרי חלב"),
    (r"בשר|עוף|הודו|דגים|נקניק|פסטרמה|קבב|שניצל", "בשר ודגים"),
    (r"קפוא", "קפואים"),
    (r"שימור|רסק|רוטב|ממרח|חמוצים|זיתים|טונה", "שימורים"),
    (r"לחם|מאפ|פיתות|לחמני|עוגה|חלה|בגט", "לחם ומאפים"),
    (r"מים|משקה|מיץ|סודה|קולה|נקטר|תרכיז", "שתייה"),
    (r"בירה|יין|וודקה|ויסקי|אלכוהול|ליקר|ערק", "אלכוהול"),
    (r"חטיף|שוקולד|ממתק|וופל|עוגי|מסטיק|במבה|ביסלי|סוכרי", "חטיפים ומתוקים"),
    (r"ניקוי|ניקיון|כביסה|נייר|אשפה|חד.פעמי|אקונומיקה|ספוג", "ניקיון"),
    (r"שמפו|סבון|טיפוח|שיניים|דאודורנט|פארם|היגיינ|חיתול", "פארם וטיפוח"),
    (r"קמח|סוכר|אורז|פסטה|קטני|תבלין|שמן|דגני|קפה|תה|אגוז|טחינה", "יבשים"),
]


def guess_dept(name):
    for pat, dept in DEPT_RULES:
        if re.search(pat, name):
            return dept
    return "אחר"


def fetch_chain(key):
    from il_supermarket_scarper.scrappers_factory import ScraperFactory
    from il_supermarket_scarper import ScarpingTask
    cfg = CHAINS[key]
    dump = f"/tmp/prices_{key}"
    ScarpingTask(
        enabled_scrapers=[getattr(ScraperFactory, cfg["scraper"])],
        dump_folder_name=dump, lookup_in_db=False,
    ).start()

    prices, promos = {}, {}
    for path in glob.glob(f"{dump}/**/*PriceFull*", recursive=True):
        try:
            with gzip.open(path, "rb") as f:
                tree = ET.parse(f)
        except Exception:
            continue
        store = tree.findtext(".//StoreName") or ""
        if cfg["store_hint"] not in store and cfg["store_hint"] not in path:
            continue
        for item in tree.iter("Item"):
            bc = item.findtext("ItemCode")
            nm = (item.findtext("ItemName") or "").strip()
            pr = float(item.findtext("ItemPrice") or 0)
            if bc and nm and pr > 0:
                prices[bc] = {"name": nm, "price": pr}
    for path in glob.glob(f"{dump}/**/*PromoFull*", recursive=True):
        try:
            with gzip.open(path, "rb") as f:
                tree = ET.parse(f)
        except Exception:
            continue
        for promo in tree.iter("Promotion"):
            desc = (promo.findtext("PromotionDescription") or "").strip()
            for it in promo.iter("ItemCode"):
                if it.text:
                    promos[it.text] = desc
    return prices, promos


def upsert(rows):
    url = f"{SUPABASE_URL}/rest/v1/products"
    headers = {
        "apikey": SERVICE_KEY,
        "Authorization": f"Bearer {SERVICE_KEY}",
        "Content-Type": "application/json",
        "Prefer": "resolution=merge-duplicates",
    }
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        r = requests.post(url, headers=headers, data=json.dumps(chunk))
        r.raise_for_status()
        print(f"upserted {i + len(chunk)}/{len(rows)}")


def main():
    vp, vpr = fetch_chain("victory")
    yp, ypr = fetch_chain("yohananof")
    barcodes = set(vp) | set(yp)
    rows = []
    for bc in barcodes:
        v, y = vp.get(bc), yp.get(bc)
        name = (v or y)["name"]
        rows.append({
            "barcode": bc,
            "name": name,
            "dept": guess_dept(name),
            "victory": v["price"] if v else None,
            "yohananof": y["price"] if y else None,
            "sale": vpr.get(bc) or ypr.get(bc),
        })
    print(f"total {len(rows)} products")
    upsert(rows)
    print("done")


if __name__ == "__main__":
    main()

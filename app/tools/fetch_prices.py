# -*- coding: utf-8 -*-
"""
צינור ייבוא מחירים אמיתיים — BLATNER  (מחירי סניף פיזי, רשמי)
==============================================================
מושך את קבצי שקיפות המחירים הרשמיים (חוק המזון) עבור הסניפים המדויקים:
  - ויקטורי, סניף צמח
  - יוחננוף, סניף אשדות יעקב / פארק אדיסון
ומעדכן את טבלת products ב-Supabase.

משתני סביבה (GitHub Secrets):
  SUPABASE_URL, SUPABASE_SERVICE_KEY   — חובה
  VICTORY_STORE_ID, YOHANANOF_STORE_ID — אופציונלי (מזהה חנות מדויק, אם נדע)

מדפיס אבחון עשיר בכל הרצה כדי לאמת דיוק.
"""
import os
import re
import gzip
import glob
import json
import xml.etree.ElementTree as ET

import requests
from il_supermarket_scarper import ScarpingTask
from il_supermarket_scarper.utils.file_types import FileTypesFilters

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

WANT = [FileTypesFilters.STORE_FILE.name,
        FileTypesFilters.PRICE_FULL_FILE.name,
        FileTypesFilters.PROMO_FULL_FILE.name]

# לכל רשת: שמות ה-scraper (מחרוזות!), רמזים לזיהוי הסניף, מזהה-חנות אופציונלי מ-env,
# ו-file_types: אילו סוגי קבצים לבקש. None = כל הסוגים (בלי סינון מוקדם — למקור החדש של ויקטורי).
CHAINS = {
    "victory": {
        "scrapers": ["VICTORY_NEW_SOURCE"],
        "hints": ["צמח"],
        "store_id_env": "VICTORY_STORE_ID",
        "file_types": None,   # בלי סינון מוקדם — ה-API של laibcatalog מפיל הכל אחרת
        "col": "victory",
    },
    "yohananof": {
        "scrapers": ["YOHANANOF"],
        "hints": ["אדיסון", "אשדות"],
        "store_id_env": "YOHANANOF_STORE_ID",
        "file_types": WANT,
        "col": "yohananof",
    },
}

DEPT_RULES = [
    (r"ירק|פרי|פירות|עשבי", "ירקות ופירות"),
    (r"חלב|גבינ|יוגורט|ביצים|מעדן|שמנת|חמאה|קוטג", "מוצרי חלב"),
    (r"בשר|עוף|הודו|דגים|נקניק|פסטרמה|קבב|שניצל", "בשר ודגים"),
    (r"קפוא", "קפואים"),
    (r"שימור|רסק|רוטב|ממרח|חמוצים|זיתים|טונה", "שימורים"),
    (r"לחם|מאפ|פית|לחמני|עוגה|חלה|בגט", "לחם ומאפים"),
    (r"מים|משקה|מיץ|סודה|קולה|נקטר|תרכיז", "שתייה"),
    (r"בירה|יין|וודקה|ויסקי|אלכוהול|ליקר|ערק", "אלכוהול"),
    (r"חטיף|שוקולד|ממתק|וופל|עוגי|מסטיק|במבה|ביסלי|סוכרי", "חטיפים ומתוקים"),
    (r"ניקוי|ניקיון|כביסה|נייר|אשפה|אקונומיקה|ספוג", "ניקיון"),
    (r"שמפו|סבון|טיפוח|שיניים|דאודורנט|פארם|היגיינ|חיתול", "פארם וטיפוח"),
    (r"קמח|סוכר|אורז|פסטה|קטני|תבלין|שמן|דגני|קפה|תה|אגוז|טחינה", "יבשים"),
]


def guess_dept(name):
    for pat, dept in DEPT_RULES:
        if re.search(pat, name):
            return dept
    return "אחר"


def open_xml(path):
    try:
        if path.endswith(".gz"):
            with gzip.open(path, "rb") as f:
                return ET.parse(f)
        return ET.parse(path)
    except Exception as e:
        print(f"  ! failed to parse {os.path.basename(path)}: {e}")
        return None


def txt(el, *tags):
    for t in tags:
        v = el.findtext(t)
        if v:
            return v.strip()
    return ""


def scrape(scraper_names, base, file_types):
    """מוריד קבצים לרשת. מנסה כל scraper. file_types=None → כל הסוגים (בלי סינון)."""
    for name in scraper_names:
        try:
            kwargs = {
                "enabled_scrapers": [name],
                "output_configuration": {"output_mode": "disk", "base_storage_path": base},
            }
            if file_types is not None:
                kwargs["files_types"] = file_types
            task = ScarpingTask(**kwargs)
            task.start()
            task.join()
        except Exception as e:
            print(f"  scraper {name} error: {e}")
    files = glob.glob(f"{base}/**/*", recursive=True)
    files = [f for f in files if os.path.isfile(f)]
    return files


def find_store(files, hints, forced_id):
    """מפרסר קבצי סניפים, מחזיר (store_id, description, all_stores)."""
    stores = {}
    for p in files:
        if not re.search(r"store", os.path.basename(p), re.I):
            continue
        t = open_xml(p)
        if t is None:
            continue
        for st in t.iter("Store"):
            sid = txt(st, "StoreId", "StoreID", "StoreID")
            nm = txt(st, "StoreName")
            city = txt(st, "City")
            addr = txt(st, "Address")
            if sid:
                stores[sid.lstrip("0") or sid] = f"{nm} | {city} | {addr}"
    if forced_id:
        key = forced_id.lstrip("0") or forced_id
        return key, stores.get(key, "(env id)"), stores
    for sid, desc in stores.items():
        if any(h in desc for h in hints):
            return sid, desc, stores
    return None, None, stores


def parse_prices(files, store_id):
    prices, promos = {}, {}
    sid = store_id.lstrip("0") or store_id
    for p in files:
        bn = os.path.basename(p)
        if sid not in re.sub(r"^0+", "", bn) and f"-{store_id}-" not in bn and sid not in bn:
            continue
        if re.search(r"price.*full|pricefull", bn, re.I):
            t = open_xml(p)
            if t is None:
                continue
            for item in t.iter("Item"):
                bc = txt(item, "ItemCode")
                nm = txt(item, "ItemName")
                try:
                    pr = float(txt(item, "ItemPrice") or 0)
                except ValueError:
                    pr = 0
                if bc and nm and pr > 0:
                    prices[bc] = {"name": nm, "price": pr}
        elif re.search(r"promo.*full|promofull", bn, re.I):
            t = open_xml(p)
            if t is None:
                continue
            for pr in t.iter("Promotion"):
                d = txt(pr, "PromotionDescription")
                if d:
                    for it in pr.iter("ItemCode"):
                        if it.text:
                            promos[it.text.strip()] = d
    return prices, promos


def fetch_chain(key):
    cfg = CHAINS[key]
    base = f"dumps_{key}"
    print(f"\n=== {key} ===")
    files = scrape(cfg["scrapers"], base, cfg.get("file_types", WANT))
    print(f"downloaded {len(files)} files")
    for f in files[:6]:
        print(f"  file sample: {os.path.basename(f)}")
    forced = os.environ.get(cfg["store_id_env"], "").strip()
    sid, desc, all_stores = find_store(files, cfg["hints"], forced)
    if not sid:
        print(f"!!! הסניף לא נמצא לפי הרמזים {cfg['hints']}.")
        print(f"    {len(all_stores)} סניפים זמינים — דוגמאות:")
        for k, v in list(all_stores.items())[:40]:
            print(f"      store_id={k}  {v}")
        return {}, {}
    print(f"סניף נבחר: store_id={sid}  {desc}")
    prices, promos = parse_prices(files, sid)
    print(f"נמשכו {len(prices)} מוצרים, {len(promos)} מבצעים")
    for probe in ["אבטיח", "חלב", "בננה"]:
        hit = next(((b, d) for b, d in prices.items() if probe in d["name"]), None)
        if hit:
            print(f"  דוגמה [{probe}]: {hit[1]['name']} = {hit[1]['price']}₪")
    return prices, promos


def upsert_chain(prices, promos, col):
    """כותב רק את עמודת המחיר של הרשת הזו (col='victory' או 'yohananof').
    עמודת הרשת השנייה לא נכללת ב-payload → נשמרת כמו שהיא (מיזוג בטוח, בלי דריסה)."""
    rows = [{
        "barcode": bc, "name": d["name"], "dept": guess_dept(d["name"]),
        col: round(d["price"], 2), "sale": promos.get(bc),
    } for bc, d in prices.items()]
    if not rows:
        print(f"  אין מה לעדכן ל-{col}")
        return
    url = f"{SUPABASE_URL}/rest/v1/products"
    headers = {"apikey": SERVICE_KEY, "Authorization": f"Bearer {SERVICE_KEY}",
               "Content-Type": "application/json", "Prefer": "resolution=merge-duplicates"}
    for i in range(0, len(rows), 500):
        chunk = rows[i:i + 500]
        r = requests.post(url, headers=headers, data=json.dumps(chunk))
        r.raise_for_status()
        print(f"  {col}: upserted {i + len(chunk)}/{len(rows)}")


def main():
    # ONLY_CHAIN=victory → רק ויקטורי (להרצה מקומית). ריק → כל הרשתות (GitHub).
    only = os.environ.get("ONLY_CHAIN", "").strip().lower()
    chains = [only] if only in CHAINS else list(CHAINS.keys())
    print(f"רשתות להרצה: {chains}")
    for key in chains:
        prices, promos = fetch_chain(key)
        upsert_chain(prices, promos, CHAINS[key]["col"])
    print("done")


if __name__ == "__main__":
    main()

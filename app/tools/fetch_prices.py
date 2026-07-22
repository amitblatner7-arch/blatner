# -*- coding: utf-8 -*-
"""
צינור ייבוא מחירים אמיתיים — BLATNER
=====================================
מושך את קבצי שקיפות המחירים הרשמיים של ויקטורי ויוחננוף (ספריית il-supermarket-scraper)
ומעדכן את טבלת products ב-Supabase עם המחירים האמיתיים והמבצעים.

רץ אוטומטית כל יום דרך GitHub Actions (.github/workflows/prices.yml).
משתני סביבה נדרשים (GitHub Secrets):
  SUPABASE_URL, SUPABASE_SERVICE_KEY
"""
import os
import re
import gzip
import glob
import json
import xml.etree.ElementTree as ET

import requests
from il_supermarket_scarper import ScarpingTask
from il_supermarket_scarper.scrappers_factory import ScraperFactory
from il_supermarket_scarper.utils.file_types import FileTypesFilters

SUPABASE_URL = os.environ["SUPABASE_URL"].rstrip("/")
SERVICE_KEY = os.environ["SUPABASE_SERVICE_KEY"]

CHAINS = {
    "victory":   {"factory": ScraperFactory.VICTORY,   "hint": "צמח"},
    "yohananof": {"factory": ScraperFactory.YOHANANOF, "hint": "אדיסון"},
}

PRICE_PROMO = [FileTypesFilters.PRICE_FULL_FILE.name, FileTypesFilters.PROMO_FULL_FILE.name]

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
    except Exception:
        return None


def parse_price_file(tree, out):
    for item in tree.iter("Item"):
        bc = (item.findtext("ItemCode") or "").strip()
        nm = (item.findtext("ItemName") or "").strip()
        try:
            pr = float(item.findtext("ItemPrice") or 0)
        except ValueError:
            pr = 0
        if bc and nm and pr > 0:
            out[bc] = {"name": nm, "price": pr}


def parse_promo_file(tree, out):
    for promo in tree.iter("Promotion"):
        desc = (promo.findtext("PromotionDescription") or "").strip()
        if not desc:
            continue
        for it in promo.iter("ItemCode"):
            if it.text:
                out[it.text.strip()] = desc


def fetch_chain(key):
    cfg = CHAINS[key]
    base = f"dumps_{key}"
    task = ScarpingTask(
        enabled_scrapers=[cfg["factory"]],
        files_types=PRICE_PROMO,
        output_configuration={"output_mode": "disk", "base_storage_path": base},
    )
    task.start()
    task.join()

    price_files = [p for p in glob.glob(f"{base}/**/*", recursive=True)
                   if re.search(r"price.*full|pricefull", os.path.basename(p), re.I)]
    promo_files = [p for p in glob.glob(f"{base}/**/*", recursive=True)
                   if re.search(r"promo.*full|promofull", os.path.basename(p), re.I)]
    print(f"[{key}] price files: {len(price_files)}, promo files: {len(promo_files)}")

    # מעדיפים את סניף המשפחה; אם לא נמצא לפי רמז השם — לוקחים את הקובץ הגדול ביותר (סניף אחד מלא)
    branch = [p for p in price_files if cfg["hint"] in p]
    chosen = branch or ([max(price_files, key=os.path.getsize)] if price_files else [])

    prices, promos = {}, {}
    for p in chosen:
        t = open_xml(p)
        if t is not None:
            parse_price_file(t, prices)
    for p in promo_files:
        t = open_xml(p)
        if t is not None:
            parse_promo_file(t, promos)
    print(f"[{key}] parsed {len(prices)} products, {len(promos)} promos")
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
            "victory": round(v["price"], 2) if v else None,
            "yohananof": round(y["price"], 2) if y else None,
            "sale": vpr.get(bc) or ypr.get(bc),
        })
    print(f"total {len(rows)} products")
    if rows:
        upsert(rows)
    print("done")


if __name__ == "__main__":
    main()

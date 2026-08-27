# -*- coding: utf-8 -*-
"""
מבצעים משתי הרשתות בעמודה אחת — מקור אמת יחיד לשני הצינורות.

הבעיה שזה פותר: לטבלה יש עמודת `sale` אחת. כל צינור כתב אליה את המבצעים
שלו, וכשלא היה מבצע הוא כתב null. התוצאה: כל בוקר יוחננוף מחק את המבצעים
של ויקטורי, ולהפך. בפועל כמעט אף מבצע לא שרד, וזו הסיבה שהמשתמשים לא ראו
מבצעים בכלל.

הפתרון בלי שינוי מבנה במסד: העמודה מחזיקה JSON עם מפתח לכל רשת.
כל צינור קורא את הערך הקיים, מעדכן רק את המפתח שלו, וכותב בחזרה.

    {"victory": "2 ב-20", "yohananof": "1+1"}

מפתח נמחק כשאין לרשת מבצע על המוצר. אובייקט ריק נשמר כ-null.
ערך ישן שאינו JSON נזרק — ממילא אי אפשר לדעת לאיזו רשת הוא שייך.
"""
import json

CHAIN_KEY = {"victory": "victory", "yohananof": "yohananof"}


def parse(raw):
    """מפרש את הערך שבעמודה. מחזיר תמיד מילון."""
    if not raw:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        val = json.loads(raw)
        return val if isinstance(val, dict) else {}
    except (ValueError, TypeError):
        return {}


def merge(existing_raw, chain, promo_text):
    """מחזיר את הערך החדש לעמודה: הקיים, כשהמפתח של הרשת הזו מעודכן."""
    data = parse(existing_raw)
    key = CHAIN_KEY.get(chain, chain)
    if promo_text:
        data[key] = promo_text
    else:
        data.pop(key, None)
    return json.dumps(data, ensure_ascii=False) if data else None


def fetch_existing(get_json, base_url, barcodes):
    """
    מושך את ערכי ה-sale הקיימים עבור הברקודים הנתונים.

    get_json הוא פונקציה שמקבלת url ומחזירה JSON, כדי שכל צינור ישתמש
    בשכבת הרשת שלו (requests אצל הענן, אותו דבר מקומית).
    """
    out = {}
    barcodes = list(barcodes)
    for i in range(0, len(barcodes), 200):
        chunk = barcodes[i:i + 200]
        quoted = ",".join('"%s"' % b.replace('"', "") for b in chunk)
        url = "%s/rest/v1/products?select=barcode,sale&barcode=in.(%s)" % (base_url, quoted)
        for row in get_json(url) or []:
            out[row["barcode"]] = row.get("sale")
    return out

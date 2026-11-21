import os
import json
import smtplib
import requests
import xml.etree.ElementTree as ET
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timezone, timedelta
from supabase import create_client
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build

SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
GEMINI_API_KEY = os.environ["GEMINI_API_KEY"].strip()
YOUTUBE_API_KEY = os.environ["GOOGLE_API_KEY"].strip()
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"].strip()
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"].strip()
SMTP_EMAIL = os.environ["SMTP_EMAIL"].strip()
SMTP_APP_PASSWORD = os.environ["SMTP_APP_PASSWORD"].strip()


def get_turkey_trends():
    yt_trends = []
    try:
        res = requests.get(
            "https://www.googleapis.com/youtube/v3/videos",
            params={"part": "snippet", "chart": "mostPopular", "regionCode": "TR", "maxResults": 12, "key": YOUTUBE_API_KEY}
        ).json()
        yt_trends = [item["snippet"]["title"] for item in res.get("items", [])]
    except Exception as e:
        print(f"YT Trend uyarısı: {e}")

    google_trends = []
    try:
        rss = requests.get("https://trends.google.com/trending/rss?geo=TR").text
        root = ET.fromstring(rss)
        google_trends = [item.text for item in root.findall(".//item/title")[:12]]
    except Exception as e:
        print(f"Google Trends uyarısı: {e}")

    return {"youtube_tr": yt_trends, "google_tr": google_trends}


def analyze_group_performance(group):
    stats = {"youtube": [], "instagram": []}
    if group.get("yt_connected") and group.get("yt_refresh_token"):
        try:
            creds = Credentials(
                token=None, refresh_token=group["yt_refresh_token"],
                token_uri="https://oauth2.googleapis.com/token",
                client_id=GOOGLE_CLIENT_ID, client_secret=GOOGLE_CLIENT_SECRET
            )
            creds.refresh(Request())
            yt = build("youtube", "v3", credentials=creds)
            search_res = yt.search().list(part="id", forMine=True, type="video", maxResults=8, order="date").execute()
            v_ids = [i["id"]["videoId"] for i in search_res.get("items", [])]
            if v_ids:
                vids = yt.videos().list(part="snippet,statistics", id=",".join(v_ids)).execute()
                for v in vids.get("items", []):
                    stats["youtube"].append({
                        "title": v["snippet"]["title"],
                        "views": int(v["statistics"].get("viewCount", 0)),
                        "likes": int(v["statistics"].get("likeCount", 0)),
                        "comments": int(v["statistics"].get("commentCount", 0))
                    })
        except Exception as e:
            print(f"YT Analiz uyarısı: {e}")

    if group.get("ig_connected") and group.get("ig_access_token"):
        try:
            ig_res = requests.get(
                f"https://graph.facebook.com/v21.0/{group['ig_business_id']}/media",
                params={"fields": "caption,media_type,like_count,comments_count", "limit": 8, "access_token": group["ig_access_token"]}
            ).json()
            for m in ig_res.get("data", []):
                stats["instagram"].append({
                    "caption": (m.get("caption") or "")[:70],
                    "type": m.get("media_type"),
                    "likes": m.get("like_count", 0),
                    "comments": m.get("comments_count", 0)
                })
        except Exception as e:
            print(f"IG Analiz uyarısı: {e}")
    return stats


def generate_ai_report(group, trends, perf_stats):
    prompt = f"""
    Sen kıdemli bir Sosyal Medya Büyüme Stratejistisin. Aşağıdaki marka grubunun verilerini ve Türkiye gündemini analiz et.
    MARKA GRUBU: {group['group_name']}
    NİŞ: {group.get('niche_keywords', 'genel')}
    YOUTUBE SON VİDEOLAR: {json.dumps(perf_stats['youtube'], ensure_ascii=False)}
    INSTAGRAM SON GÖNDERİLER: {json.dumps(perf_stats['instagram'], ensure_ascii=False)}
    TÜRKİYE YOUTUBE TRENDLERİ: {json.dumps(trends['youtube_tr'], ensure_ascii=False)}
    TÜRKİYE GOOGLE GÜNDEMİ: {json.dumps(trends['google_tr'], ensure_ascii=False)}

    GÖREV:
    1. Sayfanın mevcut içeriklerine bakarak somut 3 "Düzenleme ve İyileştirme Uyarısı" yaz.
    2. Türkiye gündemini bu markanın nişiyle birleştirerek bugün çekilebilecek 3 viral içerik fikri (Başlık, Format, İlk 3 Saniye Kancası) üret.

    SADECE JSON DÖNDÜR:
    {{
      "coach_warnings": ["Uyarı 1", "Uyarı 2", "Uyarı 3"],
      "trend_ideas": [
        {{"title": "...", "format": "Shorts / Reels", "hook": "...", "why_trending": "..."}}
      ]
    }}
    """
    models = [
        "gemini-3.8-flash",
        "gemini-flash-latest",
        "gemini-flash-lite-latest",
        "gemini-3.5-flash-lite",
    ]
    last_err = None
    for model_name in models:
        res = requests.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}",
            headers={"Content-Type": "application/json"},
            json={"contents": [{"parts": [{"text": prompt}]}], "generationConfig": {"responseMimeType": "application/json"}},
            timeout=60,
        ).json()
        if "candidates" in res:
            print(f"🤖 Gemini yanıtı alındı (model: {model_name})")
            raw_text = res["candidates"][0]["content"]["parts"][0]["text"].strip()
            if raw_text.startswith("```"):
                raw_text = raw_text.strip("`")
                if raw_text.lower().startswith("json"):
                    raw_text = raw_text[4:].strip()
            return json.loads(raw_text)
        last_err = f"{model_name}: {json.dumps(res, ensure_ascii=False)}"
        print(f"⚠️ {model_name} atlandı -> {res.get('error', {}).get('status') or res}")

    raise RuntimeError(f"Tüm Gemini modelleri başarısız oldu. Son hata: {last_err}")


def send_personalized_email(to_emails, group_name, report_data, pending_count):
    if not to_emails:
        return
    stock_banner = ""
    if pending_count < 2:
        stock_banner = f"""
        <div style='background:#fef2f2; border:1px solid #ef4444; color:#b91c1c; padding:12px; border-radius:8px; margin-bottom:16px;'>
            🚨 <strong>KRİTİK STOK UYARISI:</strong> <em>{group_name}</em> kuyruğunda sadece <strong>{pending_count}</strong> adet planlanmış içerik kaldı! Algoritma ivmesini kaybetmemek için bugün Drive'a yeni içerik yükleyin.
        </div>
        """

    warnings_html = "".join([f"<li style='margin-bottom:6px;'>⚠️ {w}</li>" for w in report_data["coach_warnings"]])
    ideas_html = "".join([
        f"""
        <div style='background:#f8fafc; border-left:4px solid #10b981; padding:12px; margin-bottom:10px; border-radius:6px;'>
            <strong>🎬 {i['title']}</strong> <span style='font-size:12px; color:#6366f1;'>({i['format']})</span><br/>
            <span style='font-size:13px;'><strong>İlk 3 Sn Kanca:</strong> "{i['hook']}"</span><br/>
            <span style='font-size:12px; color:#64748b;'><em>Trend: {i['why_trending']}</em></span>
        </div>
        """ for i in report_data["trend_ideas"]
    ])

    html = f"""
    <div style="font-family:Arial,sans-serif; max-width:640px; margin:auto; color:#1e293b;">
        <h2>🧠 [{group_name}] Günlük AI Sayfa Analizi & Trend Bülteni</h2>
        {stock_banner}
        <h3 style="color:#e11d48;">🔍 Sayfa Performans & Düzenleme Uyarıları</h3>
        <ul>{warnings_html}</ul>
        <h3 style="color:#059669;">🔥 Bugün Çekmeniz Gereken Trend İçerikler</h3>
        {ideas_html}
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{'🚨 [STOK AZALDI] ' if pending_count < 2 else ''}📊 [{group_name}] Günlük AI Analiz & Trend Raporu"
    msg["From"] = SMTP_EMAIL
    msg["To"] = ", ".join(set(to_emails))
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SMTP_EMAIL, SMTP_APP_PASSWORD)
        server.sendmail(SMTP_EMAIL, list(set(to_emails)), msg.as_string())


def run_daily_advisor():
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    trends = get_turkey_trends()
    founders = [u["email"] for u in (supabase.table("authorized_users").select("email").eq("role", "founder").execute().data or [])]
    groups = supabase.table("channel_groups").select("*").eq("is_active", True).execute().data or []
    today_str = datetime.now(timezone.utc).date().isoformat()

    for group in groups:
        # Stok sayısını kontrol et (Bekleyen gönderi sayısı)
        pending_res = supabase.table("scheduled_posts").select("id", count="exact").eq("group_id", group["id"]).eq("status", "pending").execute()
        pending_count = pending_res.count or 0
        low_stock = pending_count < 2

        perf = analyze_group_performance(group)
        report = generate_ai_report(group, trends, perf)

        supabase.table("daily_ai_insights").upsert({
            "group_id": group["id"],
            "report_date": today_str,
            "performance_summary": perf,
            "coach_warnings": report["coach_warnings"],
            "trend_ideas": report["trend_ideas"],
            "low_stock_alert": low_stock
        }, on_conflict="group_id,report_date").execute()

        recipients = list(founders)
        if group.get("manager_email"):
            recipients.append(group["manager_email"])

        send_personalized_email(recipients, group["group_name"], report, pending_count)
        print(f"✉️ Rapor ve stok durumu ({pending_count} içerik) gönderildi -> {recipients}")


if __name__ == "__main__":
    run_daily_advisor()

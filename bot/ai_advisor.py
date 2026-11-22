import os
import json
import smtplib
import requests
import xml.etree.ElementTree as ET
from urllib.parse import quote_plus
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

TR_TZ = timezone(timedelta(hours=3))


def get_turkey_trends():
    """Genel Türkiye Google Trends gündemini (ikincil bağlam olarak) çeker."""
    google_trends = []
    try:
        rss = requests.get("https://trends.google.com/trending/rss?geo=TR", timeout=15).text
        root = ET.fromstring(rss)
        google_trends = [item.text for item in root.findall(".//item/title")[:10] if item.text]
    except Exception as e:
        print(f"Google Trends uyarısı: {e}")

    return {"google_tr": google_trends}


def get_niche_live_trends(niche_keywords):
    """Markanın nişine özel son 7 gündeki yüksek izlenmeli TR YouTube videolarını ve güncel haberleri tarar."""
    raw_terms = [k.strip() for k in (niche_keywords or "genel").split(",") if k.strip()]
    query_core = " ".join(raw_terms[:3]) if raw_terms else "gündem"
    published_after = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%dT%H:%M:%SZ")

    niche_videos = []
    try:
        search_res = requests.get(
            "https://www.googleapis.com/youtube/v3/search",
            params={
                "part": "snippet",
                "q": f"{query_core} shorts OR {query_core}",
                "type": "video",
                "order": "viewCount",
                "regionCode": "TR",
                "relevanceLanguage": "tr",
                "publishedAfter": published_after,
                "maxResults": 10,
                "key": YOUTUBE_API_KEY,
            },
            timeout=20,
        ).json()
        items = search_res.get("items", [])
        vid_ids = [i["id"]["videoId"] for i in items if i.get("id", {}).get("videoId")]
        stats_map = {}
        if vid_ids:
            v_res = requests.get(
                "https://www.googleapis.com/youtube/v3/videos",
                params={"part": "statistics", "id": ",".join(vid_ids), "key": YOUTUBE_API_KEY},
                timeout=20,
            ).json()
            for v in v_res.get("items", []):
                stats_map[v["id"]] = int(v.get("statistics", {}).get("viewCount", 0))

        for item in items:
            vid = item.get("id", {}).get("videoId")
            snip = item.get("snippet", {})
            niche_videos.append({
                "title": snip.get("title", ""),
                "channel": snip.get("channelTitle", ""),
                "views_7d": stats_map.get(vid, 0),
                "published_at": (snip.get("publishedAt") or "")[:10],
            })
        niche_videos.sort(key=lambda x: x["views_7d"], reverse=True)
    except Exception as e:
        print(f"Niş YT tarama uyarısı: {e}")

    niche_news = []
    try:
        news_url = f"https://news.google.com/rss/search?q={quote_plus(query_core + ' when:7d')}&hl=tr&gl=TR&ceid=TR:tr"
        rss = requests.get(news_url, timeout=15).text
        root = ET.fromstring(rss)
        niche_news = [item.text for item in root.findall(".//item/title")[:8] if item.text]
    except Exception as e:
        print(f"Niş Google News uyarısı: {e}")

    print(f"📡 Niş taraması ({query_core}): {len(niche_videos)} güncel YT videosu, {len(niche_news)} güncel haber bulundu.")
    return {"youtube_niche_7d": niche_videos[:8], "news_niche_7d": niche_news}


def get_recent_ideas_memory(supabase, group_id, days=14):
    """Son 14 günde üretilen önerileri çekerek tekrarları engeller."""
    cutoff = (datetime.now(TR_TZ).date() - timedelta(days=days)).isoformat()
    res = supabase.table("daily_ai_insights") \
        .select("report_date, trend_ideas, coach_warnings") \
        .eq("group_id", group_id) \
        .gte("report_date", cutoff) \
        .order("report_date", desc=True) \
        .execute()

    past_titles = []
    past_warnings = []
    for row in (res.data or []):
        for idea in (row.get("trend_ideas") or []):
            if isinstance(idea, dict) and idea.get("title"):
                past_titles.append(f"[{row['report_date']}] {idea['title']}")
        for w in (row.get("coach_warnings") or []):
            if isinstance(w, str):
                past_warnings.append(w)

    return {"past_titles": past_titles[:30], "past_warnings": past_warnings[:15]}


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
                params={"fields": "caption,media_type,like_count,comments_count", "limit": 8, "access_token": group["ig_access_token"]},
                timeout=20,
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


def generate_ai_report(group, global_trends, niche_trends, perf_stats, memory):
    today_tr = datetime.now(TR_TZ).strftime("%d.%m.%Y (%A)")
    prompt = f"""
    Sen kıdemli bir Türkçe Sosyal Medya Büyüme Stratejistisin. Bugünün tarihi: {today_tr}.

    MARKA GRUBU: {group['group_name']}
    NİŞ ANAHTAR KELİMELERİ: {group.get('niche_keywords', 'genel')}

    1) SON 7 GÜNDE BU NİŞTE TÜRKİYE'DE EN ÇOK İZLENEN GERÇEK YOUTUBE VİDEOLARI:
    {json.dumps(niche_trends['youtube_niche_7d'], ensure_ascii=False)}

    2) SON 7 GÜNDE BU NİŞTEKİ GÜNCEL TÜRKİYE HABERLERİ:
    {json.dumps(niche_trends['news_niche_7d'], ensure_ascii=False)}

    3) TÜRKİYE GENEL GOOGLE GÜNDEMİ (Sadece nişe doğal uyuyorsa köprü kur):
    {json.dumps(global_trends['google_tr'], ensure_ascii=False)}

    4) MARKANIN KENDİ SON PAYLAŞIMLARI:
    YouTube: {json.dumps(perf_stats['youtube'], ensure_ascii=False)}
    Instagram: {json.dumps(perf_stats['instagram'], ensure_ascii=False)}

    5) SON 14 GÜNDE ÖNERİLEN BAŞLIKLAR — BUNLARI VEYA BENZERLERİNİ ASLA TEKRAR ETME:
    {json.dumps(memory['past_titles'], ensure_ascii=False)}

    GÖREV:
    1. Sayfanın durumuna ve rakiplerin son 7 günlük yüksek izlenmeli videolarına bakarak somut, klişeden uzak 3 "Düzenleme ve İyileştirme Uyarısı" yaz.
    2. Yukarıdaki GERÇEK son 7 günlük niş videoları / güncel haberleri referans alarak, önceki 14 günde hiç önerilmemiş 3 özgün içerik fikri üret:
       - 2 tanesi "Shorts / Reels" video formatında olsun.
       - 1 tanesi "Instagram Carousel (1. Resim Kapak + 2. Resim Mesaj)" formatında olsun ve kanca kısmında Kapak cümlesi + 2. slayt ana mesajını belirt.
    3. Her fikrin `why_trending` alanında yukarıdaki canlı verilerden HANGİ güncel video/gündemden ilham aldığını açıkça yaz.

    SADECE JSON DÖNDÜR:
    {{
      "coach_warnings": ["Uyarı 1", "Uyarı 2", "Uyarı 3"],
      "trend_ideas": [
        {{"title": "...", "format": "Shorts / Reels", "hook": "...", "why_trending": "..."}},
        {{"title": "...", "format": "Shorts / Reels", "hook": "...", "why_trending": "..."}},
        {{"title": "...", "format": "Instagram Carousel (Kapak + Mesaj)", "hook": "Kapak: ... | 2. Slayt Mesaj: ...", "why_trending": "..."}}
      ]
    }}
    """
    models = [
        "gemini-flash-lite-latest",
        "gemini-3.7-flash",
        "gemini-3.5-flash",
        "gemini-3.8-flash",
    ]
    last_err = None
    for model_name in models:
        try:
            res = requests.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model_name}:generateContent?key={GEMINI_API_KEY}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "responseMimeType": "application/json",
                        "temperature": 0.9,
                    },
                },
                timeout=35,
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
        except Exception as exc:
            last_err = f"{model_name}: {exc}"
            print(f"⚠️ {model_name} zaman aşımı/hata -> {exc}")

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
            <span style='font-size:13px;'><strong>Kanca / Slayt Akışı:</strong> "{i['hook']}"</span><br/>
            <span style='font-size:12px; color:#64748b;'><em>Canlı Trend Dayanağı: {i['why_trending']}</em></span>
        </div>
        """ for i in report_data["trend_ideas"]
    ])

    today_label = datetime.now(TR_TZ).strftime("%d.%m.%Y")
    html = f"""
    <div style="font-family:Arial,sans-serif; max-width:640px; margin:auto; color:#1e293b;">
        <h2>🧠 [{group_name}] Günlük AI Sayfa Analizi & Trend Bülteni ({today_label})</h2>
        {stock_banner}
        <h3 style="color:#e11d48;">🔍 Sayfa Performans & Düzenleme Uyarıları</h3>
        <ul>{warnings_html}</ul>
        <h3 style="color:#059669;">🔥 Bugünün Nişine Özel Canlı Trend İçerikleri</h3>
        {ideas_html}
    </div>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"{'🚨 [STOK AZALDI] ' if pending_count < 2 else ''}📊 [{group_name}] Günlük AI Analiz & Trend Raporu ({today_label})"
    msg["From"] = SMTP_EMAIL
    msg["To"] = ", ".join(set(to_emails))
    msg.attach(MIMEText(html, "html"))

    clean_email = SMTP_EMAIL.strip()
    clean_password = "".join(SMTP_APP_PASSWORD.split())
    recipient_list = list(set(to_emails))

    try:
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as server:
            server.ehlo()
            server.login(clean_email, clean_password)
            server.sendmail(clean_email, recipient_list, msg.as_string())
    except smtplib.SMTPAuthenticationError as auth_err:
        raise RuntimeError(
            f"Gmail SMTP kimlik doğrulama hatası (16 haneli Google Uygulama Şifresi gerekir): {auth_err}"
        ) from auth_err
    except (smtplib.SMTPServerDisconnected, OSError) as conn_err:
        print(f"⚠️ Port 465 bağlantısı koptu ({conn_err}), Port 587 (STARTTLS) deneniyor...")
        with smtplib.SMTP("smtp.gmail.com", 587, timeout=30) as server:
            server.ehlo()
            server.starttls()
            server.ehlo()
            server.login(clean_email, clean_password)
            server.sendmail(clean_email, recipient_list, msg.as_string())


def run_daily_advisor():
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    global_trends = get_turkey_trends()
    founders = [u["email"] for u in (supabase.table("authorized_users").select("email").eq("role", "founder").execute().data or [])]
    groups = supabase.table("channel_groups").select("*").eq("is_active", True).execute().data or []
    today_str = datetime.now(TR_TZ).date().isoformat()

    for group in groups:
        pending_res = supabase.table("scheduled_posts").select("id", count="exact").eq("group_id", group["id"]).eq("status", "pending").execute()
        pending_count = pending_res.count or 0
        low_stock = pending_count < 2

        niche_trends = get_niche_live_trends(group.get("niche_keywords"))
        memory = get_recent_ideas_memory(supabase, group["id"], days=14)
        perf = analyze_group_performance(group)
        report = generate_ai_report(group, global_trends, niche_trends, perf, memory)

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
        print(f"✉️ Rapor ve stok durumu ({pending_count} içerik) gönderildi -> {list(set(recipients))}")


if __name__ == "__main__":
    run_daily_advisor()

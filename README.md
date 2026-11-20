# AI Sosyal Medya Komuta Merkezi (Otomasyon Sistemi)

YouTube (Shorts/Video) ve Instagram (Reels/Post) için **tam otomatik zamanlı paylaşım**, **özel YouTube thumbnail eşleştirme**, **otomatik ilk yorum**, **Google Drive 15 günlük arşiv temizliği**, **SQLite senkronizasyonu**, **Sabah 09:00 Google Gemini AI Sayfa Koçu & Trend Maili + 2 Günlük Stok Alarmı** ve **Rol Bazlı Google OAuth GitHub Pages Yönetim Paneli** içeren otomasyon platformu.

## Proje Yapısı

```text
sosyal-medya-otomasyon/
├── .github/
│   └── workflows/
│       └── publisher.yml          # Saatlik paylaşım + 09:00 TR AI Koç & Stok Alarmı + SQLite commit
├── supabase/
│   ├── schema.sql                 # authorized_users, channel_groups, scheduled_posts, daily_ai_insights + RLS
│   └── functions/
│       └── brand-manager/
│           └── index.ts           # Kurucu yetkili Drive klasör yönetimi + YT/IG OAuth Edge Function
├── database/
│   └── accounts.db                # GitHub Actions tarafından senkronize edilen SQLite yedeği
├── bot/
│   ├── main.py                    # Paylaşım + İlk Yorum + Thumbnail + SQLite Senkron + 15 Gün Temizlik
│   ├── ai_advisor.py              # Sabah 09:00 AI Sayfa Koçu + TR Trend Maili + Stok Alarmı
│   ├── drive_manager.py           # Drive indirme, geçici paylaşım linki, arşivleme ve kalıcı silme
│   ├── youtube_service.py         # YouTube yükleme + Özel Thumbnail + İlk Yorum
│   └── instagram_service.py       # Instagram Reels/Post yükleme + İlk Yorum
├── docs/
│   └── index.html                 # Rol Bazlı GitHub Pages Yönetim Paneli
├── .env.example
└── requirements.txt
```

## Kurulum Özeti

1. **Supabase SQL:** `supabase/schema.sql` dosyasını Supabase SQL Editor'de çalıştırın (`SENIN_GMAIL_ADRESIN@gmail.com` değerini kendi Gmail adresinizle değiştirerek).
2. **Supabase Auth:** Authentication → Providers bölümünden **Google** sağlayıcısını aktif edip Redirect URL olarak GitHub Pages adresinizi ekleyin.
3. **Supabase Edge Function (`brand-manager`):**
   ```bash
   npx supabase login
   npx supabase link --project-ref <PROJE_REF_ID>
   npx supabase secrets set \
     GOOGLE_SERVICE_ACCOUNT_JSON='...' \
     DRIVE_QUEUE_ROOT_ID='...' \
     GOOGLE_CLIENT_ID='...' \
     GOOGLE_CLIENT_SECRET='...' \
     META_APP_ID='...' \
     META_APP_SECRET='...'
   npx supabase functions deploy brand-manager
   ```
4. **GitHub Actions Secrets:** `SUPABASE_URL`, `SUPABASE_SERVICE_ROLE_KEY`, `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `DRIVE_ARCHIVE_ROOT_ID`, `GEMINI_API_KEY`, `GOOGLE_API_KEY`, `SMTP_EMAIL`, `SMTP_APP_PASSWORD`.
5. **GitHub Pages (`docs/index.html`):** `CONFIG` nesnesindeki Supabase, Google, Meta, Gemini ve GitHub bilgilerini doldurun ve Pages kaynağını `main` / `/docs` olarak aktif edin.

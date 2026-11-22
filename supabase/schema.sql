-- 1. Yetkili Kullanıcılar (Kurucu ve Ekip)
CREATE TABLE authorized_users (
    email TEXT PRIMARY KEY,
    role TEXT NOT NULL CHECK (role IN ('founder', 'editor')),
    created_at TIMESTAMPTZ DEFAULT NOW()
);

INSERT INTO authorized_users (email, role) 
VALUES ('SENIN_GMAIL_ADRESIN@gmail.com', 'founder');

-- 2. Marka / Kanal Grupları
CREATE TABLE channel_groups (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    group_name TEXT UNIQUE NOT NULL,
    drive_folder_id TEXT UNIQUE NOT NULL,
    drive_folder_name TEXT UNIQUE NOT NULL,
    niche_keywords TEXT DEFAULT 'teknoloji, yapay zeka, trend',
    manager_email TEXT,
    yt_channel_name TEXT,
    yt_refresh_token TEXT,
    yt_connected BOOLEAN DEFAULT FALSE,
    ig_username TEXT,
    ig_business_id TEXT,
    ig_access_token TEXT,
    ig_token_expires_at TIMESTAMPTZ,
    ig_connected BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 3. Planlanan İçerikler (Esnek Format + Carousel + Alt Klasör + İlk Yorum + Thumbnail)
CREATE TABLE scheduled_posts (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    drive_file_id TEXT UNIQUE NOT NULL,
    drive_subfolder_id TEXT,
    group_id UUID REFERENCES channel_groups(id) ON DELETE CASCADE,
    folder_name TEXT NOT NULL,
    file_name TEXT NOT NULL,
    media_type TEXT NOT NULL CHECK (media_type IN ('video', 'image', 'carousel')),
    yt_format TEXT DEFAULT 'shorts' CHECK (yt_format IN ('shorts', 'video', 'none')),
    ig_format TEXT DEFAULT 'reels' CHECK (ig_format IN ('reels', 'post', 'carousel', 'none')),
    thumbnail_file_id TEXT,
    carousel_items JSONB DEFAULT '[]'::jsonb,
    title TEXT NOT NULL,
    description TEXT DEFAULT '',
    first_comment TEXT DEFAULT '',
    scheduled_at TIMESTAMPTZ NOT NULL,
    status TEXT DEFAULT 'pending' CHECK (status IN ('pending', 'partial', 'completed', 'archived_deleted')),
    progress_logs JSONB DEFAULT '{"yt": "pending", "ig": "pending"}'::jsonb,
    archived_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- 4. Günlük AI Sayfa Analizi & Trend Raporları
CREATE TABLE daily_ai_insights (
    id UUID DEFAULT gen_random_uuid() PRIMARY KEY,
    group_id UUID REFERENCES channel_groups(id) ON DELETE CASCADE,
    report_date DATE DEFAULT CURRENT_DATE,
    performance_summary JSONB DEFAULT '{}'::jsonb,
    coach_warnings TEXT[],
    trend_ideas JSONB DEFAULT '[]'::jsonb,
    low_stock_alert BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(group_id, report_date)
);

-- 5. Satır Bazlı Güvenlik (RLS)
ALTER TABLE authorized_users ENABLE ROW LEVEL SECURITY;
ALTER TABLE channel_groups ENABLE ROW LEVEL SECURITY;
ALTER TABLE scheduled_posts ENABLE ROW LEVEL SECURITY;
ALTER TABLE daily_ai_insights ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION get_my_role()
RETURNS TEXT AS $$
  SELECT role FROM authorized_users WHERE email = (auth.jwt() ->> 'email');
$$ LANGUAGE sql SECURITY DEFINER;

CREATE POLICY "Yetkililer listeyi okuyabilir" ON authorized_users FOR SELECT USING (get_my_role() IN ('founder', 'editor'));
CREATE POLICY "Sadece kurucu ekip yonetir" ON authorized_users FOR ALL USING (get_my_role() = 'founder');
CREATE POLICY "Yetkililer gruplari gorebilir" ON channel_groups FOR SELECT USING (get_my_role() IN ('founder', 'editor'));
CREATE POLICY "Sadece kurucu gruplari yonetir" ON channel_groups FOR ALL USING (get_my_role() = 'founder');
CREATE POLICY "Ekip icerik planlayabilir" ON scheduled_posts FOR ALL USING (get_my_role() IN ('founder', 'editor'));
CREATE POLICY "Yetkililer analizleri gorebilir" ON daily_ai_insights FOR SELECT USING (get_my_role() IN ('founder', 'editor'));

-- 6. Rol Yetkileri (service_role ve authenticated için tablo izinleri)
GRANT USAGE ON SCHEMA public TO anon, authenticated, service_role;
GRANT ALL ON TABLE public.authorized_users TO service_role;
GRANT ALL ON TABLE public.channel_groups TO service_role;
GRANT ALL ON TABLE public.scheduled_posts TO service_role;
GRANT ALL ON TABLE public.daily_ai_insights TO service_role;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.authorized_users TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.channel_groups TO authenticated;
GRANT SELECT, INSERT, UPDATE, DELETE ON TABLE public.scheduled_posts TO authenticated;
GRANT SELECT ON TABLE public.daily_ai_insights TO authenticated;
GRANT EXECUTE ON FUNCTION public.get_my_role() TO authenticated, service_role;

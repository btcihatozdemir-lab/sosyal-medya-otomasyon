import os
import sqlite3
from datetime import datetime, timezone, timedelta
from supabase import create_client
from drive_manager import DriveManager
from youtube_service import upload_to_youtube
from instagram_service import upload_to_instagram

DB_PATH = "database/accounts.db"
SUPABASE_URL = os.environ["SUPABASE_URL"].strip()
SUPABASE_KEY = os.environ["SUPABASE_SERVICE_ROLE_KEY"].strip()
GOOGLE_CLIENT_ID = os.environ["GOOGLE_CLIENT_ID"].strip()
GOOGLE_CLIENT_SECRET = os.environ["GOOGLE_CLIENT_SECRET"].strip()
GOOGLE_SA_JSON = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"].strip()
DRIVE_ARCHIVE_ROOT_ID = os.environ["DRIVE_ARCHIVE_ROOT_ID"].strip()


def sync_sqlite_from_supabase(supabase):
    os.makedirs("database", exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS channel_groups (
            id TEXT PRIMARY KEY,
            group_name TEXT,
            drive_folder_id TEXT,
            drive_folder_name TEXT,
            niche_keywords TEXT,
            manager_email TEXT,
            yt_channel_name TEXT,
            yt_refresh_token TEXT,
            yt_connected INTEGER,
            ig_username TEXT,
            ig_business_id TEXT,
            ig_access_token TEXT,
            ig_connected INTEGER,
            synced_at TEXT
        )
    ''')

    groups = supabase.table("channel_groups").select("*").execute().data or []
    c.execute("DELETE FROM channel_groups")
    now_str = datetime.now(timezone.utc).isoformat()

    for g in groups:
        c.execute('''
            INSERT INTO channel_groups VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            g["id"], g["group_name"], g["drive_folder_id"], g["drive_folder_name"],
            g.get("niche_keywords"), g.get("manager_email"),
            g.get("yt_channel_name"), g.get("yt_refresh_token"), 1 if g.get("yt_connected") else 0,
            g.get("ig_username"), g.get("ig_business_id"), g.get("ig_access_token"),
            1 if g.get("ig_connected") else 0, now_str
        ))

    conn.commit()
    conn.close()
    print(f"🔄 SQLite kasası senkronize edildi ({len(groups)} marka grubu).")
    return {g["id"]: g for g in groups}


def cleanup_old_archives(supabase, drive):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    res = supabase.table("scheduled_posts") \
        .select("id, drive_file_id, thumbnail_file_id, file_name") \
        .eq("status", "completed") \
        .lt("archived_at", cutoff) \
        .execute()

    for post in (res.data or []):
        print(f"🗑️ 15 günü dolan arşiv siliniyor: {post['file_name']}")
        if drive.delete_file_permanently(post["drive_file_id"]):
            if post.get("thumbnail_file_id"):
                drive.delete_file_permanently(post["thumbnail_file_id"])
            supabase.table("scheduled_posts").update({"status": "archived_deleted"}).eq("id", post["id"]).execute()


def run():
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)
    drive = DriveManager(GOOGLE_SA_JSON, DRIVE_ARCHIVE_ROOT_ID)

    groups_map = sync_sqlite_from_supabase(supabase)
    cleanup_old_archives(supabase, drive)

    now_iso = datetime.now(timezone.utc).isoformat()
    posts = supabase.table("scheduled_posts") \
        .select("*") \
        .in_("status", ["pending", "partial"]) \
        .lte("scheduled_at", now_iso) \
        .order("scheduled_at") \
        .execute().data or []

    if not posts:
        print("⏳ Şu an paylaşılacak zamanı gelmiş içerik yok.")
        return

    os.makedirs("tmp_media", exist_ok=True)

    for post in posts:
        group = groups_map.get(post["group_id"])
        if not group:
            continue

        print(f"\n🚀 Grup: [{group['group_name']}] | Dosya: {post['file_name']}")
        logs = post.get("progress_logs") or {"yt": "pending", "ig": "pending"}
        yt_fmt = post.get("yt_format", "shorts")
        ig_fmt = post.get("ig_format", "reels")

        local_path = None
        thumb_path = None
        public_url = None
        perm_id = None

        try:
            # 1. YOUTUBE PAYLAŞIMI (Bağlıysa ve format 'none' değilse)
            if group.get("yt_connected") and yt_fmt != "none" and logs.get("yt") != "done":
                if post["media_type"] != "video":
                    logs["yt"] = "skipped: sadece video"
                else:
                    try:
                        local_path = drive.download_file(post["drive_file_id"], f"tmp_media/{post['file_name']}")
                        if post.get("thumbnail_file_id"):
                            thumb_path = drive.download_file(post["thumbnail_file_id"], f"tmp_media/thumb_{post['file_name']}.jpg")

                        yt_id = upload_to_youtube(
                            group["yt_refresh_token"], GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET,
                            local_path, post["title"], post["description"],
                            yt_format=yt_fmt, thumb_path=thumb_path, first_comment=post.get("first_comment")
                        )
                        logs["yt"] = "done"
                        print(f"  ✅ YouTube ({yt_fmt}) Yüklendi: {yt_id}")
                    except Exception as e:
                        logs["yt"] = f"error: {str(e)[:120]}"
                        print(f"  ❌ YouTube Hatası: {e}")
            else:
                if logs.get("yt") != "done":
                    logs["yt"] = "skipped"

            # 2. INSTAGRAM PAYLAŞIMI (Bağlıysa ve format 'none' değilse)
            if group.get("ig_connected") and ig_fmt != "none" and logs.get("ig") != "done":
                try:
                    public_url, perm_id = drive.set_public_link(post["drive_file_id"])
                    ig_id = upload_to_instagram(
                        group["ig_business_id"], group["ig_access_token"],
                        public_url, post["media_type"], ig_fmt,
                        post["title"], post["description"], first_comment=post.get("first_comment")
                    )
                    logs["ig"] = "done"
                    print(f"  ✅ Instagram ({ig_fmt}) Yüklendi: {ig_id}")
                except Exception as e:
                    logs["ig"] = f"error: {str(e)[:120]}"
                    print(f"  ❌ Instagram Hatası: {e}")
            else:
                if logs.get("ig") != "done":
                    logs["ig"] = "skipped"

        finally:
            if perm_id:
                drive.remove_public_link(post["drive_file_id"], perm_id)
            if local_path and os.path.exists(local_path):
                os.remove(local_path)
            if thumb_path and os.path.exists(thumb_path):
                os.remove(thumb_path)

        yt_done = logs["yt"] in ("done", "skipped") or str(logs["yt"]).startswith("skipped")
        ig_done = logs["ig"] in ("done", "skipped") or str(logs["ig"]).startswith("skipped")

        update_data = {"progress_logs": logs}
        if yt_done and ig_done:
            drive.move_to_archive(post["drive_file_id"])
            if post.get("thumbnail_file_id"):
                drive.move_to_archive(post["thumbnail_file_id"])
            update_data["status"] = "completed"
            update_data["archived_at"] = datetime.now(timezone.utc).isoformat()
            print(f"📦 Tamamlandı! Dosya 'Paylasilanlar' klasörüne taşındı.")
        else:
            update_data["status"] = "partial"

        supabase.table("scheduled_posts").update(update_data).eq("id", post["id"]).execute()


if __name__ == "__main__":
    run()

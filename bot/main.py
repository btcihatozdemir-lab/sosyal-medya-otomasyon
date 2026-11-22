import os
import sqlite3
from datetime import datetime, timezone, timedelta
from PIL import Image
from supabase import create_client
from drive_manager import DriveManager
from youtube_service import upload_to_youtube
from instagram_service import upload_to_instagram, upload_carousel_to_instagram

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
    # Güvenlik: OAuth refresh_token ve access_token değerleri SQLite dosyasına yazılmaz.
    c.execute("DROP TABLE IF EXISTS channel_groups")
    c.execute('''
        CREATE TABLE channel_groups (
            id TEXT PRIMARY KEY,
            group_name TEXT,
            drive_folder_id TEXT,
            drive_folder_name TEXT,
            niche_keywords TEXT,
            manager_email TEXT,
            yt_channel_name TEXT,
            yt_connected INTEGER,
            ig_username TEXT,
            ig_business_id TEXT,
            ig_connected INTEGER,
            synced_at TEXT
        )
    ''')

    groups = supabase.table("channel_groups").select("*").execute().data or []
    now_str = datetime.now(timezone.utc).isoformat()

    for g in groups:
        c.execute('''
            INSERT INTO channel_groups VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            g["id"], g["group_name"], g["drive_folder_id"], g["drive_folder_name"],
            g.get("niche_keywords"), g.get("manager_email"),
            g.get("yt_channel_name"), 1 if g.get("yt_connected") else 0,
            g.get("ig_username"), g.get("ig_business_id"),
            1 if g.get("ig_connected") else 0, now_str
        ))

    conn.commit()
    conn.close()
    print(f"🔄 SQLite kasası (tokensız/güvenli) senkronize edildi ({len(groups)} marka grubu).")
    return {g["id"]: g for g in groups}


def convert_to_jpeg(raw_path, jpg_path):
    """PNG/WebP/RGBA görselleri Instagram uyumlu RGB JPEG formatına çevirir."""
    with Image.open(raw_path) as im:
        if im.mode in ("RGBA", "LA", "P"):
            im_rgba = im.convert("RGBA")
            bg = Image.new("RGB", im_rgba.size, (255, 255, 255))
            bg.paste(im_rgba, mask=im_rgba.split()[-1])
            rgb_im = bg
        else:
            rgb_im = im.convert("RGB")
        rgb_im.save(jpg_path, "JPEG", quality=95, optimize=True)
    return jpg_path


def prepare_instagram_image_url(drive, file_id, file_name, mime_type, temp_local_files, temp_drive_ids, perms_to_remove):
    """JPEG ise doğrudan geçici link açar; PNG/WebP ise otomatik JPEG'e çevirip geçici Drive linki verir."""
    if not mime_type:
        try:
            meta = drive.get_file_metadata(file_id)
            mime_type = meta.get("mimeType", "")
            file_name = file_name or meta.get("name", "")
        except Exception:
            mime_type = ""

    is_jpeg = (mime_type == "image/jpeg") or str(file_name).lower().endswith((".jpg", ".jpeg"))
    if is_jpeg:
        url, perm_id = drive.set_public_link(file_id)
        perms_to_remove.append((file_id, perm_id))
        return url

    print(f"  🔄 JPEG'e dönüştürülüyor: {file_name} ({mime_type or 'bilinmiyor'})")
    raw_path = f"tmp_media/raw_{file_id}"
    jpg_path = f"tmp_media/conv_{file_id}.jpg"
    drive.download_file(file_id, raw_path)
    temp_local_files.append(raw_path)

    convert_to_jpeg(raw_path, jpg_path)
    temp_local_files.append(jpg_path)

    temp_id = drive.upload_temp_jpeg(jpg_path, f"tmp_ig_{file_id}.jpg")
    temp_drive_ids.append(temp_id)

    url, perm_id = drive.set_public_link(temp_id)
    perms_to_remove.append((temp_id, perm_id))
    return url


def cleanup_old_archives(supabase, drive):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=15)).isoformat()
    res = supabase.table("scheduled_posts") \
        .select("id, drive_file_id, drive_subfolder_id, thumbnail_file_id, carousel_items, file_name") \
        .eq("status", "completed") \
        .lt("archived_at", cutoff) \
        .execute()

    for post in (res.data or []):
        print(f"🗑️ 15 günü dolan arşiv siliniyor: {post['file_name']}")
        deleted_ok = False
        if post.get("drive_subfolder_id"):
            deleted_ok = drive.delete_file_permanently(post["drive_subfolder_id"])
        elif post.get("carousel_items"):
            results = [drive.delete_file_permanently(item["file_id"]) for item in post["carousel_items"] if item.get("file_id")]
            deleted_ok = any(results)
        else:
            deleted_ok = drive.delete_file_permanently(post["drive_file_id"])
            if deleted_ok and post.get("thumbnail_file_id"):
                drive.delete_file_permanently(post["thumbnail_file_id"])

        if deleted_ok:
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

        print(f"\n🚀 Grup: [{group['group_name']}] | Tür: {post['media_type']} | Dosya: {post['file_name']}")
        logs = post.get("progress_logs") or {"yt": "pending", "ig": "pending"}
        yt_fmt = post.get("yt_format", "shorts")
        ig_fmt = post.get("ig_format", "reels")

        temp_local_files = []
        temp_drive_ids = []
        perms_to_remove = []

        try:
            # 1. YOUTUBE PAYLAŞIMI (Bağlıysa ve format 'none' değilse)
            if group.get("yt_connected") and yt_fmt != "none" and logs.get("yt") != "done":
                if post["media_type"] != "video":
                    logs["yt"] = "skipped: sadece video"
                else:
                    try:
                        local_path = drive.download_file(post["drive_file_id"], f"tmp_media/{post['file_name']}")
                        temp_local_files.append(local_path)
                        thumb_path = None
                        if post.get("thumbnail_file_id"):
                            raw_thumb = drive.download_file(
                                post["thumbnail_file_id"],
                                f"tmp_media/raw_thumb_{post['thumbnail_file_id']}"
                            )
                            temp_local_files.append(raw_thumb)
                            thumb_path = convert_to_jpeg(raw_thumb, f"tmp_media/thumb_{post['thumbnail_file_id']}.jpg")
                            temp_local_files.append(thumb_path)

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

            # 2. INSTAGRAM PAYLAŞIMI (Video / Tekil Görsel / Sıralı Carousel + Otomatik PNG->JPEG)
            if group.get("ig_connected") and ig_fmt != "none" and logs.get("ig") != "done":
                try:
                    if post["media_type"] == "carousel" or ig_fmt == "carousel":
                        slides = post.get("carousel_items") or []
                        if len(slides) < 2:
                            raise ValueError("Carousel gönderisinde en az 2 görsel bulunmalıdır.")
                        public_urls = []
                        for slide in slides:
                            url = prepare_instagram_image_url(
                                drive,
                                slide["file_id"],
                                slide.get("file_name", ""),
                                slide.get("mime_type", ""),
                                temp_local_files,
                                temp_drive_ids,
                                perms_to_remove,
                            )
                            public_urls.append(url)
                        ig_id = upload_carousel_to_instagram(
                            group["ig_business_id"], group["ig_access_token"],
                            public_urls, post["title"], post["description"],
                            first_comment=post.get("first_comment")
                        )
                        logs["ig"] = "done"
                        print(f"  ✅ Instagram Carousel ({len(public_urls)} slayt) Yüklendi: {ig_id}")
                    elif post["media_type"] == "image":
                        public_url = prepare_instagram_image_url(
                            drive,
                            post["drive_file_id"],
                            post.get("file_name", ""),
                            "",
                            temp_local_files,
                            temp_drive_ids,
                            perms_to_remove,
                        )
                        ig_id = upload_to_instagram(
                            group["ig_business_id"], group["ig_access_token"],
                            public_url, "image", ig_fmt,
                            post["title"], post["description"], first_comment=post.get("first_comment")
                        )
                        logs["ig"] = "done"
                        print(f"  ✅ Instagram Fotoğraf Yüklendi: {ig_id}")
                    else:
                        public_url, perm_id = drive.set_public_link(post["drive_file_id"])
                        perms_to_remove.append((post["drive_file_id"], perm_id))
                        ig_id = upload_to_instagram(
                            group["ig_business_id"], group["ig_access_token"],
                            public_url, "video", ig_fmt,
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
            for fid, pid in perms_to_remove:
                drive.remove_public_link(fid, pid)
            for temp_fid in temp_drive_ids:
                drive.delete_file_permanently(temp_fid)
            for lp in temp_local_files:
                if lp and os.path.exists(lp):
                    os.remove(lp)

        yt_done = logs["yt"] in ("done", "skipped") or str(logs["yt"]).startswith("skipped")
        ig_done = logs["ig"] in ("done", "skipped") or str(logs["ig"]).startswith("skipped")

        update_data = {"progress_logs": logs}
        if yt_done and ig_done:
            if post.get("drive_subfolder_id"):
                drive.move_to_archive(post["drive_subfolder_id"])
            elif post["media_type"] == "carousel" and post.get("carousel_items"):
                for slide in post["carousel_items"]:
                    drive.move_to_archive(slide["file_id"])
            else:
                drive.move_to_archive(post["drive_file_id"])
                if post.get("thumbnail_file_id"):
                    drive.move_to_archive(post["thumbnail_file_id"])
            update_data["status"] = "completed"
            update_data["archived_at"] = datetime.now(timezone.utc).isoformat()
            print("📦 Tamamlandı! Dosya/klasör 'Paylasilanlar' arşivine taşındı.")
        else:
            update_data["status"] = "partial"

        supabase.table("scheduled_posts").update(update_data).eq("id", post["id"]).execute()


if __name__ == "__main__":
    run()

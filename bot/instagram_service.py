import time
import requests

GRAPH_API = "https://graph.facebook.com/v21.0"


def upload_to_instagram(ig_business_id, access_token, public_media_url, media_type, ig_format, title, description, first_comment=None):
    caption = f"{title}\n\n{description}".strip()
    payload = {"caption": caption, "access_token": access_token}

    if media_type == "video":
        if ig_format == "reels":
            payload["media_type"] = "REELS"
        else:
            payload["media_type"] = "VIDEO"
        payload["video_url"] = public_media_url
    else:
        payload["image_url"] = public_media_url

    res = requests.post(f"{GRAPH_API}/{ig_business_id}/media", data=payload).json()
    if "id" not in res:
        raise Exception(f"IG Konteyner Hatası: {res}")

    creation_id = res["id"]

    # İşlenme durumunu bekle
    for _ in range(30):
        time.sleep(10)
        st = requests.get(
            f"{GRAPH_API}/{creation_id}",
            params={"fields": "status_code,status", "access_token": access_token}
        ).json()
        if st.get("status_code") == "FINISHED":
            break
        elif st.get("status_code") == "ERROR":
            raise Exception(f"IG İşleme Hatası: {st}")

    pub = requests.post(
        f"{GRAPH_API}/{ig_business_id}/media_publish",
        data={"creation_id": creation_id, "access_token": access_token}
    ).json()

    if "id" not in pub:
        raise Exception(f"IG Yayınlama Hatası: {pub}")

    media_id = pub["id"]

    # İlk Yorum (First Comment) varsa at
    if first_comment and first_comment.strip():
        try:
            requests.post(
                f"{GRAPH_API}/{media_id}/comments",
                data={"message": first_comment.strip(), "access_token": access_token}
            )
            print(f"  💬 Instagram ilk yorum eklendi.")
        except Exception as e:
            print(f"  ⚠️ Instagram yorum uyarısı: {e}")

    return media_id

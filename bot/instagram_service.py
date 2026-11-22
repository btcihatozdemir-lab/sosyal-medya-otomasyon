import time
import requests

GRAPH_API = "https://graph.facebook.com/v21.0"


def _wait_for_container(creation_id, access_token, max_tries=30, delay_sec=10):
    for _ in range(max_tries):
        st = requests.get(
            f"{GRAPH_API}/{creation_id}",
            params={"fields": "status_code,status", "access_token": access_token},
            timeout=30,
        ).json()
        code = st.get("status_code")
        if code == "FINISHED":
            return
        if code == "ERROR":
            raise Exception(f"IG İşleme Hatası: {st}")
        time.sleep(delay_sec)


def _post_first_comment(media_id, access_token, first_comment):
    if first_comment and first_comment.strip():
        try:
            requests.post(
                f"{GRAPH_API}/{media_id}/comments",
                data={"message": first_comment.strip(), "access_token": access_token},
                timeout=30,
            )
            print("  💬 Instagram ilk yorum eklendi.")
        except Exception as e:
            print(f"  ⚠️ Instagram yorum uyarısı: {e}")


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

    res = requests.post(f"{GRAPH_API}/{ig_business_id}/media", data=payload, timeout=60).json()
    if "id" not in res:
        raise Exception(f"IG Konteyner Hatası: {res}")

    creation_id = res["id"]
    if media_type == "video":
        time.sleep(10)
        _wait_for_container(creation_id, access_token, max_tries=30, delay_sec=10)
    else:
        time.sleep(3)
        _wait_for_container(creation_id, access_token, max_tries=12, delay_sec=3)

    pub = requests.post(
        f"{GRAPH_API}/{ig_business_id}/media_publish",
        data={"creation_id": creation_id, "access_token": access_token},
        timeout=60,
    ).json()

    if "id" not in pub:
        raise Exception(f"IG Yayınlama Hatası: {pub}")

    media_id = pub["id"]
    _post_first_comment(media_id, access_token, first_comment)
    return media_id


def upload_carousel_to_instagram(ig_business_id, access_token, public_media_urls, title, description, first_comment=None):
    """2–10 adet sıralı JPEG görseli tek bir Instagram Carousel gönderisi olarak yayınlar."""
    if not public_media_urls or len(public_media_urls) < 2:
        raise Exception("Instagram Carousel için en az 2 görsel gereklidir.")

    child_ids = []
    for idx, url in enumerate(public_media_urls[:10], start=1):
        child_res = requests.post(
            f"{GRAPH_API}/{ig_business_id}/media",
            data={
                "image_url": url,
                "is_carousel_item": "true",
                "access_token": access_token,
            },
            timeout=60,
        ).json()
        if "id" not in child_res:
            raise Exception(f"IG Carousel slayt #{idx} konteyner hatası: {child_res}")
        child_id = child_res["id"]
        time.sleep(2)
        _wait_for_container(child_id, access_token, max_tries=12, delay_sec=3)
        child_ids.append(child_id)
        print(f"  🖼️ Carousel slayt #{idx} hazır: {child_id}")

    caption = f"{title}\n\n{description}".strip()
    parent_res = requests.post(
        f"{GRAPH_API}/{ig_business_id}/media",
        data={
            "media_type": "CAROUSEL",
            "children": ",".join(child_ids),
            "caption": caption,
            "access_token": access_token,
        },
        timeout=60,
    ).json()
    if "id" not in parent_res:
        raise Exception(f"IG Carousel ana konteyner hatası: {parent_res}")

    parent_id = parent_res["id"]
    time.sleep(3)
    _wait_for_container(parent_id, access_token, max_tries=15, delay_sec=4)

    pub = requests.post(
        f"{GRAPH_API}/{ig_business_id}/media_publish",
        data={"creation_id": parent_id, "access_token": access_token},
        timeout=60,
    ).json()
    if "id" not in pub:
        raise Exception(f"IG Carousel yayınlama hatası: {pub}")

    media_id = pub["id"]
    _post_first_comment(media_id, access_token, first_comment)
    return media_id
